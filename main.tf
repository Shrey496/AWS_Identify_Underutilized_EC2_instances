terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 4.0"
    }
    archive = {
      source  = "hashicorp/archive"                      #Used to archive the main.py file to a .zip file format for Lambda use
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = "us-east-2"
}

# --- Input Variables ---
variable "google_sheet_key" {
  description = "The key from the Google Sheet URL"
  type        = string
  default     = "1GuqpdVRx8_KdI7LfmwNBEsV6UUe4oF4YmoZ_Lhf8_ko" 
}

variable "google_secret_arn" {
  description = "The ARN of the AWS Secret holding the Google credentials"
  type        = string
}

variable "gspread_layer_arn" {
  description = "The ARN of the manually uploaded gspread Lambda Layer"
  type        = string
}

# 1. Zip the Python script
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/main.py"        # Points to the main.py file
  output_path = "${path.module}/lambda_function.zip"   #File format ready to be uploaded to Lambda
}

# 2. IAM Role
resource "aws_iam_role" "lambda_role" {
  name = "gsheet-rightsizing-lambda-role"               #Name of the Lambda IAM role
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }       
    }]
  })
}

# 3. IAM Policy
resource "aws_iam_policy" "lambda_policy" {
  name = "gsheet-rightsizing-policy"                #Name of the policy that will be attached to the Lambda role
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeRegions", "ec2:DescribeInstances", "ec2:DescribeTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["cloudwatch:GetMetricData", "cloudwatch:ListMetrics"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = var.google_secret_arn 
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach_policy" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# 4. Lambda Function
resource "aws_lambda_function" "rightsizing_lambda" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "gsheet-ec2-rightsizing-reporter"  #Lambda function name
  role             = aws_iam_role.lambda_role.arn
  handler          = "main.lambda_handler"          # Points to main.py -> lambda_handler function in main.py hence, it instructs Lambda to the starting point of the code
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = "python3.10"
  timeout          = 300
  memory_size      = 256

  environment {
    variables = {
      GOOGLE_SHEET_KEY  = var.google_sheet_key
      GOOGLE_SECRET_ARN = var.google_secret_arn
    }
  }
  
  layers = [var.gspread_layer_arn] # Attaches the gspread libraries, Lambda inherently does not have access to them
}

# 5. Schedule
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "gsheet-weekly-wednesday-report" # Eventbridge rule name
  description         = "Triggers GSheets rightsizing report every Wednesday at 11 AM CST"
  schedule_expression = "cron(0 17 ? * WED *)"
}

resource "aws_cloudwatch_event_target" "trigger_lambda" {  #The scheduler points to the function, imposing the cron setup
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "lambda-gsheet"
  arn       = aws_lambda_function.rightsizing_lambda.arn
}

resource "aws_lambda_permission" "allow_cloudwatch" {      #
  statement_id  = "AllowExecutionFromCloudWatch-GSheet"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rightsizing_lambda.function_name
  principal     = "events.amazonaws.com"     #Internal name of the scheduler service
  source_arn    = aws_cloudwatch_event_rule.schedule.arn   #Only allows this rule to trigger Lambda
}