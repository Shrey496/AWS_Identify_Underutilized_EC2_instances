# AWS_Identify_Underutilized_EC2_instances
This repository contains boto3 code to help identify underutilized EC2 instances of size **.medium** and above on the basis of Average CPU Utilization and Average CPU Credits.

# Prerequisites

* Python 3.x installed
* Ideally, using a virtual environment before installing boto3 is recommended since it helps install project-specific packages using pip without affecting the global Python installation
  * `python -m venv .venv` - This command uses the **venv** module, which is part of Python's standard library, to create a new virtual environment in a directory named **.venv** within the current directory
  * `source .venv/bin/activate` - After successful activation, the terminal prompt will change to include **(.venv)**, indicating that an active isolated virtual environment
* Boto3 library installed: pip install boto3
* AWS Credentials configured: Your environment (local machine or AWS Lambda) must have the necessary AWS credentials configured, for example, via aws configure or an IAM Role
* IAM Permissions: The principal/user running this script will at least need the following permissions:
  * ec2:DescribeInstances (Gets a detailed list of all EC2 instances and their properties (like their `ID`, `type`, and `tags`)
  * ec2:DescribeRegions (Gets a list of all available AWS regions (like `us-east-1`, `us-west-2`, etc)
  * cloudwatch:GetMetricData (Gets performance metrics like `CPUUtilization`)
 

