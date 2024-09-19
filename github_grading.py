import csv
import os
import logging
import requests
from github import Github, GithubException
import zipfile
import io

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# GitHub API configuration
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
REPO_OWNER = 'CSC241Fall24'
ASSIGNMENT_PREFIX = 'week2-'

# CSV file configurations
GITHUB_CSV = 'week2-grades-1726764975.csv'
GRADE_CSV_FILES = [
    'Fall 2024 Data Structures & Algorithms (CSC-241-01)_GradesExport_2024-09-19-16-31.csv',
    'Fall 2024 Data Structures & Algorithms (CSC-241-02)_GradesExport_2024-09-19-16-40.csv'  # Assuming second class
]

# Define the grade field name as a constant
GRADE_FIELD = 'homework1 Points Grade <Numeric MaxPoints:6 Category:Homework>'

def load_github_data(filename):
    github_data = {}
    with open(filename, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            github_username = row['github_username'].strip()
            github_data[github_username] = {
                'roster_identifier': row['roster_identifier'].strip(),
                'student_repository_name': row['student_repository_name'].strip()
            }
    return github_data

def load_grade_data(filenames):
    grade_data = {}
    grade_files = {}
    for filename in filenames:
        with open(filename, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                student_id = row['OrgDefinedId'].strip()
                grade_data[student_id] = row
                grade_files[student_id] = filename  # Map student ID to their grade file
    return grade_data, grade_files

def match_students(github_data, grade_data):
    matched_data = {}
    for github_username, github_info in github_data.items():
        roster_identifier = github_info['roster_identifier']
        for student_id, grade_info in grade_data.items():
            full_name = f"{grade_info['Last Name'].strip()}, {grade_info['First Name'].strip()}"
            if roster_identifier.startswith(full_name):
                matched_data[github_username] = {
                    'student_id': student_id,
                    'full_name': full_name,
                    'repo_name': github_info['student_repository_name']
                }
                break
        else:
            logging.warning(f"No matching grade data found for GitHub user: {github_username} with roster identifier: {roster_identifier}")
    return matched_data

def get_workflow_run_logs(repo, run_id):
    try:
        run = repo.get_workflow_run(run_id)
        logs_url = run.logs_url
        headers = {'Authorization': f'token {GITHUB_TOKEN}'}
        response = requests.get(logs_url, headers=headers)
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                log_contents = ""
                for filename in z.namelist():
                    with z.open(filename) as f:
                        log_contents += f.read().decode('utf-8') + "\n"
                return log_contents
        else:
            logging.error(f"Failed to fetch logs: HTTP {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"Error getting workflow run logs: {e}")
        return None

def extract_error_from_logs(logs):
    """Extract the error details from the logs."""
    if logs:
        error_messages = []
        for line in logs.splitlines():
            if "Error:" in line or "error" in line.lower():
                error_messages.append(line.strip())
        if error_messages:
            return "\n".join(error_messages)
        else:
            return "No specific error details found."
    return "No logs found."

def check_repository_status(g, repo_name):
    try:
        repo = g.get_repo(f"{REPO_OWNER}/{repo_name}")
        workflows = list(repo.get_workflows())

        if not workflows:
            logging.info("No workflows found in the repository.")
            return False, "No workflows found"

        for workflow in workflows:
            runs = list(workflow.get_runs(branch=repo.default_branch))
            if runs:
                latest_run = runs[0]
                logging.info(f"Latest workflow run status: {latest_run.conclusion}")

                if latest_run.conclusion.lower() != 'success':
                    logs = get_workflow_run_logs(repo, latest_run.id)
                    error_details = extract_error_from_logs(logs)
                    return False, error_details

        return True, "All tests passed"
    except GithubException as e:
        logging.error(f"GitHub API error: {e}")
        return False, f"Error: {str(e)}"

def update_grade_csv(filename, student_id, grade):
    rows = []
    fieldnames = []
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames
            for row in reader:
                if row['OrgDefinedId'].strip() == student_id:
                    row[GRADE_FIELD] = grade
                rows.append(row)
    except Exception as e:
        logging.error(f"Error reading grade CSV ({filename}): {e}")
        return

    try:
        with open(filename, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        logging.error(f"Error writing to grade CSV ({filename}): {e}")

def main():
    if not GITHUB_TOKEN:
        logging.error("GITHUB_TOKEN environment variable is not set.")
        return

    # Prompt the user to decide whether to re-grade all students
    while True:
        user_input = input("Do you want to re-grade all students? (y/n): ").strip().lower()
        if user_input in ['y', 'yes']:
            regrade_all = True
            break
        elif user_input in ['n', 'no']:
            regrade_all = False
            break
        else:
            print("Please enter 'y' or 'n'.")

    g = Github(GITHUB_TOKEN)

    github_data = load_github_data(GITHUB_CSV)
    grade_data, grade_files = load_grade_data(GRADE_CSV_FILES)
    matched_data = match_students(github_data, grade_data)

    # Check if specific students are being skipped and log their details
    # Adding debug logs to trace the issue
    skipped_students = []

    for github_username, student_info in matched_data.items():
        student_id = student_info['student_id']
        full_name = student_info['full_name']
        repo_name = student_info['repo_name']
        grade_file = grade_files.get(student_id)

        if not grade_file:
            logging.warning(f"No grade file found for student: {full_name} (GitHub: {github_username})")
            continue

        # Check if we should skip this student
        if not regrade_all:
            existing_grade = grade_data[student_id].get(GRADE_FIELD, "").strip()
            if existing_grade:
                logging.info(f"Skipping {full_name} (GitHub: {github_username}) as they already have a grade: {existing_grade}")
                continue

        logging.info(f"Processing student: {full_name} (GitHub: {github_username})")

        status, message = check_repository_status(g, repo_name)
        logging.info(f"Repository status: {'Passed' if status else 'Failed'}")

        if status:
            suggested_grade = '6'
            logging.info(f"All tests passed. Suggested grade: {suggested_grade}")
        else:
            suggested_grade = '0'
            logging.info(f"Tests failed. Error details:")
            logging.info(message)
            logging.info(f"Suggested grade: {suggested_grade}")

        # Prompt for grade input with default suggestion
        grade = input(f"Enter grade for {full_name} (default: {suggested_grade}): ").strip() or suggested_grade
        update_grade_csv(grade_file, student_id, grade)
        logging.info(f"Grade {grade} recorded for {full_name}")
        print("-----")

    # Identify and log skipped students
    for github_username, github_info in github_data.items():
        if github_username not in matched_data:
            logging.warning(f"Student with GitHub username '{github_username}' was not matched to any grade data.")

if __name__ == "__main__":
    main()
