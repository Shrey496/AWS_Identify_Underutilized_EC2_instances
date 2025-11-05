# Prerequisites

# Python 3.x installed.
# Boto3 library installed: pip install boto3.
# AWS Credentials configured: Your environment (local machine or AWS Lambda) must have the necessary AWS credentials configured, for example, via aws configure or an IAM Role.
# IAM Permissions: The principal running this script will need permissions to:
# ec2:DescribeInstances
# ec2:DescribeRegions
# cloudwatch:GetMetricData 


# Step 1: Set up the Python environment and Boto3 clients

# Create a Python file named rightsizing_ec2.py. This script will use Boto3 to interact with EC2 and CloudWatch. 

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


# --- Modified for new logic ---
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

# --- NEW Function to write the report to a CSV file ---
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
    
    # --- Modified to write to file instead of printing ---
    write_report_to_csv(report_data)

# import boto3
# from datetime import datetime, timedelta
# import collections
# import json

# # Define the reporting period and CPU threshold
# REPORTING_PERIOD_DAYS = 30
# CPU_THRESHOLD = 20  # Percentage

# # Initialize Boto3 clients
# ec2_client = boto3.client('ec2')
# cw_client = boto3.client('cloudwatch')

# # --- Helper function for recommendations ---
# def get_recommendation(instance_type):
#     """Provides a basic, naive recommendation for a smaller instance size."""
#     # This is a very simple map and doesn't cover all families.
#     # It's a starting point for a recommendation.
#     size_map = {
#         '16xlarge': '12xlarge',
#         '12xlarge': '8xlarge',
#         '8xlarge': '4xlarge',
#         '4xlarge': '2xlarge',
#         '2xlarge': 'xlarge',
#         'xlarge': 'large',
#         'large': 'medium',
#         'medium': 'small',
#         'small': 'micro',
#         'micro': 'nano',
#     }
    
#     try:
#         # Step 1: Split 't2.micro' into 't2' (family) and 'micro' (size)
#         family, size = instance_type.split('.')
        
#         # Step 2: Look up 'micro' in the size_map
#         recommended_size = size_map.get(size)
        
#         if recommended_size:
#             # Step 3: Rebuild the new name, e.g., 't2' + '.' + 'nano'
#             return f"{family}.{recommended_size}"
#         else:
#             # This happens if 'nano' (the smallest) is passed in
#             return f"Review manually (no smaller size in map)"
            
#     except ValueError:
#         return "Review manually (complex type)"

# # --- Modified to capture InstanceType and Name tag ---
# def get_running_instances():
#     """Retrieves all running EC2 instances across all regions."""
#     instances = collections.defaultdict(list)
#     regions = [region['RegionName'] for region in ec2_client.describe_regions()['Regions']]

#     for region in regions:
#         ec2 = boto3.client('ec2', region_name=region)
#         reservations = ec2.describe_instances(Filters=[
#             {'Name': 'instance-state-name', 'Values': ['running']}
#         ])['Reservations']

#         for reservation in reservations:
#             for instance in reservation['Instances']:
                
#                 # --- Find the 'Name' tag ---
#                 instance_name = 'N/A'  # Default if no 'Name' tag
#                 if 'Tags' in instance:
#                     for tag in instance['Tags']:
#                         if tag['Key'] == 'Name':
#                             instance_name = tag['Value']
#                             break
#                 # -------------------------
                
#                 instance_info = {
#                     'InstanceId': instance['InstanceId'],
#                     'InstanceType': instance['InstanceType'],
#                     'InstanceName': instance_name
#                 }
#                 instances[region].append(instance_info)
#     return instances

# def get_cpu_utilization(instance_id, region):
#     """Fetches average CPU utilization for a given instance."""
#     cw = boto3.client('cloudwatch', region_name=region)
#     end_time = datetime.utcnow()
#     start_time = end_time - timedelta(days=REPORTING_PERIOD_DAYS)

#     response = cw.get_metric_data(
#         MetricDataQueries=[
#             {
#                 'Id': 'm1',
#                 'MetricStat': {
#                     'Metric': {
#                         'Namespace': 'AWS/EC2',
#                         'MetricName': 'CPUUtilization',
#                         'Dimensions': [
#                             {
#                                 'Name': 'InstanceId',
#                                 'Value': instance_id
#                             },
#                         ]
#                     },
#                     'Period': 86400,  # One data point per day
#                     'Stat': 'Average',
#                 },
#                 'ReturnData': True,
#             },
#         ],
#         StartTime=start_time,
#         EndTime=end_time
#     )

#     if response['MetricDataResults'] and response['MetricDataResults'][0]['Values']:
#         return sum(response['MetricDataResults'][0]['Values']) / len(response['MetricDataResults'][0]['Values'])
#     return 0.0

# # --- Modified to remove creator ---
# def generate_rightsizing_report(instances):
#     """Generates a report of underutilized instances."""
#     underutilized_instances = []
#     for region, instance_details in instances.items():
        
#         for instance in instance_details:
#             instance_id = instance['InstanceId']
#             instance_type = instance['InstanceType']
#             instance_name = instance['InstanceName']
            
#             avg_cpu = get_cpu_utilization(instance_id, region)
            
#             if avg_cpu < CPU_THRESHOLD:
#                 recommendation = get_recommendation(instance_type)
                
#                 underutilized_instances.append({
#                     'InstanceId': instance_id,
#                     'InstanceName': instance_name,
#                     'Region': region,
#                     'InstanceType': instance_type,
#                     'AverageCPU': f"{avg_cpu:.2f}%",
#                     'Recommendation': recommendation
#                 })

#     return underutilized_instances

# if __name__ == '__main__':
#     all_running_instances = get_running_instances()
#     report = generate_rightsizing_report(all_running_instances)

#     if report:
#         print("\n--- Underutilized EC2 Instances Report ---")
#         for item in report:
#             # --- Updated final print statement (Creator removed) ---
#             print(f"Instance ID: {item['InstanceId']}, Name: {item['InstanceName']}, Region: {item['Region']}, Current Type: {item['InstanceType']}, Avg. CPU: {item['AverageCPU']}, Recommended Type: {item['Recommendation']}")
#     else:
#         print("\nNo underutilized instances found based on the defined criteria.")


# Step 5: Automate the instance modification (with caution)

# This part is optional and should be implemented with extreme care and human approval. It adds the functionality to stop, resize, and start the instances flagged in the report. 

# Create a new file resize_instance.py for this potentially destructive action. 



# python

# # # resize_instance.py



# import boto3

# import time



# def resize_ec2_instance(instance_id, new_instance_type, region):

#     """Stops, resizes, and starts an EC2 instance."""

#     print(f"Resizing instance {instance_id} in {region} to {new_instance_type}...")

#     ec2 = boto3.client('ec2', region_name=region)



#     try:

#         # Stop the instance

#         print("  - Stopping instance...")

#         ec2.stop_instances(InstanceIds=[instance_id])

#         waiter = ec2.get_waiter('instance_stopped')

#         waiter.wait(InstanceIds=[instance_id])

#         print("  - Instance stopped.")



#         # Modify the instance type

#         print("  - Changing instance type...")

#         ec2.modify_instance_attribute(InstanceId=instance_id, InstanceType={'Value': new_instance_type})

#         print("  - Instance type changed.")



#         # Start the instance

#         print("  - Starting instance...")

#         ec2.start_instances(InstanceIds=[instance_id])

#         waiter = ec2.get_waiter('instance_running')

#         waiter.wait(InstanceIds=[instance_id])

#         print("  - Instance started successfully.")

#     except Exception as e:

#         print(f"  - Error resizing instance {instance_id}: {e}")



# if __name__ == '__main__':

#     # **WARNING**: THIS IS A DESTRUCTIVE ACTION. DO NOT RUN UNTIL YOU HAVE CAREFULLY REVIEWED THE REPORT!

#     # Example usage:

#     # resize_ec2_instance('i-0123456789abcdef0', 't3.micro', 'us-east-1')

    

#     print("This script is for demonstration purposes. Uncomment the function call to run.")

# # Use code with caution.





# How to use this solution

# Generate the report: First, run rightsizing_ec2.py. This provides a non-destructive list of potential targets.
# Review the report: Manually inspect the instances flagged by the report. Confirm that they are indeed underutilized and that it's safe to change their type. You can use a mapping to suggest a smaller instance size (e.g., m5.large -> m5.medium).
# Automate with caution: If you choose to automate the resizing, use the resize_instance.py script. For production, you might want to wrap this logic in a Lambda function that only performs changes on tagged instances during a scheduled maintenance window.