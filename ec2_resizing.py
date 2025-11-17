#Set up the Python environment and Boto3 clients

import boto3
from datetime import datetime, timedelta
import collections
import csv  # Added to write the CSV file

# --- Define Thresholds and Ignore List ---
REPORTING_PERIOD_DAYS = 30
CPU_THRESHOLD = 20      # Average CPU percentage
CPU_CREDIT_THRESHOLD = 100 # Avg balance for a 'T' instance

# Instance sizes to ignore (will not be in the report)
IGNORE_SIZES = ['small', 'micro', 'nano']

# Initialize Boto3 clients
ec2_client = boto3.client('ec2')

# --- Helper function for recommendations ---
def get_recommendation(instance_type):
    """Provides a basic, naive recommendation for a smaller general-purpose instance size."""
    size_map = {
        '16xlarge': '12xlarge',
        '12xlarge': '8xlarge',
        '8xlarge': '4xlarge',
        '4xlarge': '2xlarge',
        '2xlarge': 'xlarge',
        'xlarge': 'large',
        'large': 'medium',
        'medium': 'small', # This is the lowest it will recommend
    }
    
    try:
        family, size = instance_type.split('.')
        recommended_size = size_map.get(size)
        
        if recommended_size:
            if recommended_size in IGNORE_SIZES:
                 return f"Review manually (next step-down is {recommended_size})"
            return f"{family}.{recommended_size}"
        else:
            return f"Review manually (no smaller size in map)"
            
    except ValueError:
        return "Review manually (complex type)"

# --- Modified to capture InstanceType and Name tag ---
def get_running_instances():
    """Retrieves all running EC2 instances across all regions."""
    instances = collections.defaultdict(list)
    regions = [region['RegionName'] for region in ec2_client.describe_regions()['Regions']]
    print("Finding running instances...")

    for region in regions:
        ec2 = boto3.client('ec2', region_name=region)
        reservations = ec2.describe_instances(Filters=[
            {'Name': 'instance-state-name', 'Values': ['running']}
        ])['Reservations']

        for reservation in reservations:
            for instance in reservation['Instances']:
                
                instance_name = 'N/A'
                if 'Tags' in instance:
                    for tag in instance['Tags']:
                        if tag['Key'] == 'Name':
                            instance_name = tag['Value']
                            break
                
                instance_info = {
                    'InstanceId': instance['InstanceId'],
                    'InstanceType': instance['InstanceType'],
                    'InstanceName': instance_name
                }
                instances[region].append(instance_info)
    print("...Finished finding instances.")
    return instances

# --- Modified to get only CPU and CPU Credits ---
def get_instance_metrics(instance_id, region):
    """
    Fetches average CPU and CPU Credit balance.
    Returns a dict: {'cpu_avg': float, 'cpu_credit_avg': float or str}
    """
    cw = boto3.client('cloudwatch', region_name=region)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=REPORTING_PERIOD_DAYS)
    
    # Initialize metrics with defaults
    metrics = {
        'cpu_avg': 0.0,
        'cpu_credit_avg': "N/A" # Will only be populated for 'T' instances
    }
    
    dims = [{'Name': 'InstanceId', 'Value': instance_id}]
    
    try:
        response = cw.get_metric_data(
            MetricDataQueries=[
                # --- CPU ---
                {'Id': 'm_cpu', 'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'CPUUtilization', 'Dimensions': dims}, 'Period': 86400, 'Stat': 'Average'}, 'ReturnData': True},
                # --- CPU Credits (Average balance over the period) ---
                {'Id': 'm_cpu_credit', 'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'CPUCreditBalance', 'Dimensions': dims}, 'Period': 86400, 'Stat': 'Average'}, 'ReturnData': True},
            ],
            StartTime=start_time,
            EndTime=end_time
        )
        
        for result in response['MetricDataResults']:
            if result['Values']:
                value = sum(result['Values']) / len(result['Values'])
                
                if result['Id'] == 'm_cpu':
                    metrics['cpu_avg'] = value
                elif result['Id'] == 'm_cpu_credit':
                    metrics['cpu_credit_avg'] = value

    except Exception as e:
        print(f"    Warning: Could not get full CloudWatch data for {instance_id}: {e}")
        pass

    return metrics

def generate_rightsizing_report(instances):
    """Generates a report of underutilized instances."""
    underutilized_instances = []
    print("Generating rightsizing report...")

    for region, instance_details in instances.items():
        
        for instance in instance_details:
            instance_id = instance['InstanceId']
            instance_type = instance['InstanceType']
            instance_name = instance['InstanceName']
            
            # --- Check 1: Ignore small instances ---
            try:
                family, size = instance_type.split('.')
                if size in IGNORE_SIZES:
                    continue # Skip this instance entirely
            except Exception:
                continue # Skip if we can't parse the type
            
            # --- Check 2: Get Metrics ---
            metrics = get_instance_metrics(instance_id, region)
            avg_cpu = metrics['cpu_avg']
            avg_cpu_credits = metrics['cpu_credit_avg']
            
            # --- Check 3: Analyze Metrics ---
            recommendation = "N/A"
            is_underutilized = False
            
            # --- Smart 'T' Instance Check ---
            # Is it a 'T' instance with a low credit balance?
            if instance_type.startswith('t') and isinstance(avg_cpu_credits, float) and avg_cpu_credits < CPU_CREDIT_THRESHOLD:
                # Low CPU might be misleading because it's being throttled.
                # Do NOT recommend downsizing.
                recommendation = "Needs Review (Low CPU Credit Balance)"
            
            # --- Standard Underutilization Check ---
            elif avg_cpu < CPU_THRESHOLD:
                # Low CPU
                is_underutilized = True
                recommendation = get_recommendation(instance_type)
            
            # --- Add to report if underutilized or needs review ---
            if is_underutilized or recommendation != "N/A":
                report_item = {
                    'InstanceId': instance_id,
                    'InstanceName': instance_name,
                    'Region': region,
                    'InstanceType': instance_type,
                    'AvgCPU': f"{avg_cpu:.2f}%",
                    'AvgCPUCredits': f"{avg_cpu_credits:.0f}" if isinstance(avg_cpu_credits, float) else avg_cpu_credits,
                    'Recommendation': recommendation
                }
                underutilized_instances.append(report_item)

    return underutilized_instances

# Create a CSV file and write the report to it
def write_report_to_csv(report, filename="report.csv"):
    """Writes the report (a list of dictionaries) to a CSV file."""
    if not report:
        print("No underutilized instances found. No report generated.")
        return
        
    # Get the headers from the keys of the first item in the report
    headers = report[0].keys()
    
    try:
        with open(filename, 'w', newline='') as output_file:
            writer = csv.DictWriter(output_file, fieldnames=headers)
            writer.writeheader()
            writer.writerows(report)
        print(f"\nSuccessfully generated report: {filename}")
    except Exception as e:
        print(f"\nError writing to CSV file: {e}")


if __name__ == '__main__':
    all_running_instances = get_running_instances()
    report_data = generate_rightsizing_report(all_running_instances)
    
    # --- Write to the CSV file ---
    write_report_to_csv(report_data)