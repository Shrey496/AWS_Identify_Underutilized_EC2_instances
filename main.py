import boto3
import collections
import os
import gspread  # This library comes from the Lambda Layer
import gspread.utils 
import json
from google.oauth2.service_account import Credentials # From Layer
from datetime import datetime, timedelta, timezone

# --- Configuration ---
REPORTING_PERIOD_DAYS = 30
CPU_THRESHOLD = 20
CPU_CREDIT_THRESHOLD = 100
IGNORE_SIZES = ['small', 'micro', 'nano']

# --- Environment Variables (Will be set by Terraform) ---
SHEET_KEY = os.environ['GOOGLE_SHEET_KEY'] 
SECRET_ARN = os.environ['GOOGLE_SECRET_ARN'] 

# Initialize Clients
ec2_client = boto3.client('ec2')
secrets_client = boto3.client('secretsmanager')

# --- Google Sheets Functions ---
def authenticate_gspread():
    print("Authenticating with Google...")
    secret_response = secrets_client.get_secret_value(SecretId=SECRET_ARN)
    creds_json = json.loads(secret_response['SecretString'])
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    gc = gspread.authorize(creds)
    print("Google authentication successful.")
    return gc

def write_to_sheet(gc, report_data):
    try:
        sh = gc.open_by_key(SHEET_KEY)
        sheet_name = datetime.now(timezone.utc).strftime("%m/%d/%y")
        print(f"Creating new worksheet named: {sheet_name}")
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
        
        if not report_data:
            worksheet.update('A1', [["No underutilized instances found."]]) # Fixed format
            print("No underutilized instances found.")
            return

        # Prepare data for upload
        headers = list(report_data[0].keys())
        values = [list(d.values()) for d in report_data]
        full_data_list = [headers] + values
        
        worksheet.update('A1', full_data_list, value_input_option='USER_ENTERED')
        print(f"Successfully wrote {len(report_data)} rows to sheet '{sheet_name}'.")

        # --- Formatting Section ---
        print("Applying table formatting...")
        num_rows = len(full_data_list)
        num_cols = len(headers)
        end_cell = gspread.utils.rowcol_to_a1(num_rows, num_cols)
        header_end_cell = gspread.utils.rowcol_to_a1(1, num_cols)
        data_range = f"A1:{end_cell}"

        header_format = {"backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}, "horizontalAlignment": "CENTER", "textFormat": {"bold": True}}
        all_cells_format = {"borders": {"top": {"style": "SOLID_MEDIUM"}, "bottom": {"style": "SOLID_MEDIUM"}, "left": {"style": "SOLID_MEDIUM"}, "right": {"style": "SOLID_MEDIUM"}}}
        
        worksheet.format(f"A1:{header_end_cell}", header_format)
        worksheet.format(data_range, all_cells_format)
        
        body = {"requests": [{"autoResizeDimensions": {"dimensions": {"sheetId": worksheet.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": num_cols}}}]}
        sh.batch_update(body)
        print("Applied formatting and auto-resized columns.")

    except gspread.exceptions.APIError as e:
        if "already exists" in str(e):
            print(f"Sheet '{sheet_name}' already exists. Skipping.")
        else:
            print(f"A gspread API error occurred: {e}")
            raise
    except Exception as e:
        print(f"An error occurred writing to the sheet: {e}")
        raise

# --- AWS Functions ---

def get_recommendation(instance_type):
    # This map is now the only place that controls downsizing.
    # We will not recommend a size smaller than 'small'.
    size_map = {
        '32xlarge': '24xlarge', '24xlarge': '16xlarge',
        '16xlarge': '12xlarge', '12xlarge': '8xlarge', '8xlarge': '4xlarge',
        '4xlarge': '2xlarge', '2xlarge': 'xlarge', 'xlarge': 'large',
        'large': 'medium', 'medium': 'small' 
    }
    try:
        family, size = instance_type.split('.')
        recommended = size_map.get(size)
        
        if recommended:
            return f"{family}.{recommended}"
        
        # If the size isn't in the map (like 'small', 'micro', 'nano'), 
        # it will return "Review manually"
        return "Review manually"
        
    except ValueError:
        return "Review manually"

def get_running_instances():
    instances = collections.defaultdict(list)
    try:
        regions = [r['RegionName'] for r in ec2_client.describe_regions()['Regions']]
    except Exception as e:
        print(f"Error describing regions: {e}")
        return {}
        
    for region in regions:
        ec2 = boto3.client('ec2', region_name=region)
        try:
            reservations = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])['Reservations']
            for res in reservations:
                for inst in res['Instances']:
                    name = 'N/A'
                    if 'Tags' in inst:
                        for tag in inst['Tags']:
                            if tag['Key'] == 'Name': name = tag['Value']; break
                    instances[region].append({
                        'InstanceId': inst['InstanceId'],
                        'InstanceType': inst['InstanceType'],
                        'InstanceName': name
                    })
        except Exception as e:
            print(f"Skipping region {region}: {str(e)}")
    return instances

# -----------------------------------------------------------------
# UPDATED FUNCTION: get_instance_metrics
# -----------------------------------------------------------------
def get_instance_metrics(instance_id, region):
    cw = boto3.client('cloudwatch', region_name=region)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=REPORTING_PERIOD_DAYS)
    metrics = {'cpu_avg': 0.0, 'cpu_credit_avg': "N/A"}
    dims = [{'Name': 'InstanceId', 'Value': instance_id}]
    try:
        resp = cw.get_metric_data(
            MetricDataQueries=[
                {'Id': 'm_cpu', 'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'CPUUtilization', 'Dimensions': dims}, 'Period': 86400, 'Stat': 'Average'}, 'ReturnData': True},
                {'Id': 'm_cred', 'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'CPUCreditBalance', 'Dimensions': dims}, 'Period': 86400, 'Stat': 'Average'}, 'ReturnData': True},
            ],
            StartTime=start, EndTime=end
        )
        for res in resp['MetricDataResults']:
            if res['Values']:
                val = sum(res['Values']) / len(res['Values'])
                if res['Id'] == 'm_cpu': metrics['cpu_avg'] = val
                elif res['Id'] == 'm_cred': metrics['cpu_credit_avg'] = val
                
    # --- ADDED ERROR LOGGING ---
    except Exception as e:
        print(f"Error getting metrics for {instance_id} in {region}: {e}")
        # We will pass, but the error will be in the CloudWatch logs
        # The function will return the default (0.0 CPU)
        pass 
        
    return metrics

# -----------------------------------------------------------------
# UPDATED FUNCTION: generate_report
# -----------------------------------------------------------------
def generate_report(instances):
    data = []
    for region, inst_list in instances.items():
        for inst in inst_list:
            try:
                family, size = inst['InstanceType'].split('.')
                if size in IGNORE_SIZES: continue
            except: continue
  
            metrics = get_instance_metrics(inst['InstanceId'], region)
            rec = "N/A"
            underutilized = False
            
            if inst['InstanceType'].startswith('t') and isinstance(metrics['cpu_credit_avg'], float) and metrics['cpu_credit_avg'] < CPU_CREDIT_THRESHOLD:
                rec = "Needs Review (Low Credits)"
            elif metrics['cpu_avg'] < CPU_THRESHOLD:
                underutilized = True
                rec = get_recommendation(inst['InstanceType'])
     
            if isinstance(metrics['cpu_credit_avg'], float):
                # 1. Round the float to 0 decimal places (e.g., 250.7 -> 251.0)
                rounded_value = round(metrics['cpu_credit_avg'], 0)
                # 2. Convert the float to an integer (e.g., 251.0 -> 251)
                int_value = int(rounded_value)
                # 3. Convert the integer to a string (e.g., 251 -> "251")
                credits_str = str(int_value)
            else:
                credits_str = metrics['cpu_credit_avg']

            if underutilized or rec != "N/A":
                data.append({
                    'InstanceId': inst['InstanceId'], 'Region': region,
                    'InstanceType': inst['InstanceType'], 'Name': inst['InstanceName'],
                    'Avg.CPU%': f"{metrics['cpu_avg']:.2f}", 'Avg.CPUCredits': f"{metrics['cpu_credit_avg']}",
                    'Recommendation': rec
                })
    return data

# --- Lambda Handler ---
def lambda_handler(event, context):
    print("Starting Rightsizing Analysis (Google Sheets)...")
    
    instances = get_running_instances()
    report_data = generate_report(instances)
    
    gspread_client = authenticate_gspread()
    # This will now pass an empty list [] if no instances are found,
    # or the full report data if they are.
    write_to_sheet(gspread_client, report_data) 
    
    if report_data:
        return {"status": "Success", "count": len(report_data)}