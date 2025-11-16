# AWS_Identify_Underutilized_EC2_instances
This project provides an automated, serverless solution to identify and report on underutilized EC2 instances, helping to reduce cloud costs.

# Task
Identifying Underutilized EC2 instances, and notify the users that created them in order to consider rightsizing (downsizing) the instances or terminate them if no longer in use.

# Execution
The solution is built on **AWS Lambda (boto3)** and is deployed and managed entirely via **Terraform**. It runs on an automatic weekly schedule, scans all EC2 instances across all AWS regions, and publishes a formatted report to a central **Google Spreadsheet**. A **Slack Workflow** then automatically notifies the team with two links: the updated report and the **EC2 Global Search** page for easy review and direct access.


# Prerequisites

### On the Local Machine

* Git (to clone this repo)
* Python 3.10+ (to build the Lambda layer)
* Ideally, using a virtual environment before installing boto3 is recommended since it helps install project-specific packages using pip without affecting the global Python installation
  * `python -m venv .venv` - This command uses the **venv** module, which is part of Python's standard library, to create a new virtual environment in a directory named **.venv** within the current directory
  * `source .venv/bin/activate` - After successful activation, the terminal prompt will change to include **(.venv)**, indicating that an active isolated virtual environment
* Boto3 library installed: pip3 install boto3
* AWS CLI

### Cloud Accounts & Services
* AWS Credentials configured: Your environment (local machine or AWS Lambda) must have the necessary AWS credentials configured, for example, via aws configure or an IAM Role with permissions to create IAM roles, Lambda functions, Secrets Manager secrets, and EventBridge rules
* Google Cloud Project: A GCP project to create service account credentials
* Google Spreadsheet: A Google Spreadsheet where each new weekly report is added as its own distinct, date-stamped sheet
* Slack Workspace: (Optional) A workplace where you have permission to create workflows


#### Permissions
IAM Permissions: The AWS Lambda role will need the following permissions:
  * `ec2:DescribeInstances`: Gets a detailed list of all EC2 instances and their properties (like their `ID`, `type`, and `tags`)
  * `ec2:DescribeRegions`: Gets a list of all available AWS regions (like `us-east-1`, `us-west-2`, etc)
  * `cloudwatch:GetMetricData`: Gets performance metrics like `CPUUtilization`
  * `secretsmanager:GetSecretValue`: To securely fetch the Google credentials (the JSON key file) from AWS Secrets Manager
  * `logs:CreateLogStream` & `logs:PutLogEvents`: To allow the Lambda function to write its output to AWS CloudWatch logs for debugging purposes
  * `logs:CreateLogGroup`: To create a new log group for the Lambda function when it runs for the first time



# Setup Guide

### Part 1: Google Cloud Setup (Manual)
A **Service Account** must be created for **AWS Lambda** to execute it's task.
1. **Login to the Google Cloud Console**.
2. **Enable APIs**: Navigate to **APIs & Services** > **Library**. Find and **Enable** these two APIs:
    * **Google Drive API**
    
    <img width="854" height="314" alt="Google Drive API" src="https://github.com/user-attachments/assets/ad303b77-6e18-4b34-a4d3-db563e7deffe" />
    



    * **Google sheets API**
      
    <img width="706" height="346" alt="Google Sheets API" src="https://github.com/user-attachments/assets/bdd7bbed-98f9-42b9-bf8b-6603c43afc70" />

3. **Create Service Account**
    * Navigate to **APIs & Services** > **Credentials**
    * Click on **Create Credentials** > **Service account**
    * Give it a name and click **Done**

      <img width="702" height="630" alt="x C" src="https://github.com/user-attachments/assets/056862d3-fa1e-447d-bae1-04a0bd9ff2dd" />

4. **Generate JSON Key**:
    * Select the created service account
    * Go to the **Keys** tab > **Add Key** > **Create new key**
    
    <img width="1069" height="573" alt="Screenshot 2025-11-16 at 12 39 50 AM" src="https://github.com/user-attachments/assets/7c2721a4-25e9-4bb0-91ac-5e847ec615b6" />

    
    * Select **JSON** and click **Create**

    <img width="585" height="343" alt="Create private key for &#39;aws-lambda-g-sheets&#39;" src="https://github.com/user-attachments/assets/fed7bb86-90ba-4af8-bbd6-dc16a440b291" />

    * A JSON key file will download. **Save this file securely**

### Part 2: Google Spreadsheet Setup (Manual)
 1. **Open the downloaded JSON key file** with a text editor.
 2. Copy the `client_email` value (e.g., `aws-lambda-gsheets-writer@...iam.gserviceaccount.com`).
 3. Open the Google Sheet.
 4. Click the **Share** button in the top right.
 5. Paste the `client_email` into the **Add people** box and give it **Editor** permissions.

 <img width="520" height="434" alt="+ Share Underutilized EC2 instances" src="https://github.com/user-attachments/assets/44c9a0d7-0f1e-454e-8a95-5e64e6c2135b" />


### Part 3: AWS Setup (Manual)
The goal is to securely store the Google key and package the required Python libraries.

1. **Store the Google Key in AWS Secrets Manager**:

   * Go to the **AWS Secrets Manager console**
   * Click **Store a new secret** > **Other type of secret**
   * In the **Plaintext** tab, paste the entire contents of the JSON key file
   * Name the secret (e.g., `google-sheets-creds`)
   * After storing, click on the secret and copy the **Secret ARN**
  
 2. **Create the Lambda Layer**:
    * On the local computer, open a terminal and run these commands:
```
mkdir -p gspread_layer/python
pip3 install gspread google-auth -t ./gspread_layer/python
cd gspread_layer && zip -r ../gspread_layer.zip .
```
    
   * Go to the **AWS Lambda console** > **Layers** > **Create layer**
   * Name it (e.g., `gspread-layer-v1`)
   * Upload the `gspread_layer.zip` file
    
<img width="1018" height="703" alt="Layer configuration" src="https://github.com/user-attachments/assets/616de787-41c0-47f5-8a09-979bbe882804" />

    
   * Select the `python3.10` runtime
   * Click **Create** and copy the **Layer ARN**

### Part 4: Terraform deployment
1. In the project directory, create a file named `terraform.tfvars` and paste the following, filling in the values genrated in the previous steps:
`terraform.tfvars`

```
# Paste the ARN from AWS Secrets Manager
google_secret_arn = "arn:aws:secretsmanager:us-east-1:1234567890..:secret:google-sheets-creds-AbCdEf"

# Paste the ARN from the AWS Lambda Layer
gspread_layer_arn = "arn:aws:lambda:us-east-1:1234567890..:layer:gspread-layer-v1:1"

# Paste the Key from your Google Sheet URL
google_sheet_key = "1GuqpdVRx8_KdI7LfmwNBEsV..."

```
2. Ensure `main.py` and `main.tf` present in this repository are located within the same directory.
3. **Initialize Terraform and deploy the stack**

   ```
   terraform init
   terraform apply -var-file="terraform.tfvars"
   ```

### Part 5: Test the function
1. Go to the **AWS Lambda Console** and find the created function (`gsheet-ec2-rightsizing-reporter`).
2. Go to the **Test** tab, create a new test event and click on **Test** to test the function.
3. Once the execution has finished successfully, check the **Google Spreadsheet** There should be a new tab/sheet with today's date and the formatted report.

<img width="505" height="225" alt="Status Succeeded" src="https://github.com/user-attachments/assets/b020f56c-c0b8-4304-9866-a5abb5ce7bdb" />

<img width="749" height="81" alt="Type" src="https://github.com/user-attachments/assets/f50893aa-4e66-445d-aca0-0f9e1f430c9f" />

