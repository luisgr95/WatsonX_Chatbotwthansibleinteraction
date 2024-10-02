import os
import pandas as pd
import time
from dotenv import load_dotenv
from ibm_watson_machine_learning.foundation_models import Model
from ibm_watson_machine_learning.metanames import GenTextParamsMetaNames as GenParams
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from bs4 import BeautifulSoup

# Disable SSL warnings
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Load environment variables
load_dotenv()
api_key = os.getenv("API_KEY", None)
ibm_cloud_url = os.getenv("IBM_CLOUD_URL", None)
project_id = os.getenv("PROJECT_ID", None)
ansible_tower_url = os.getenv("ANSIBLE_TOWER_URL", None)
ansible_tower_token = os.getenv("ANSIBLE_TOWER_TOKEN", None)

if api_key is None or ibm_cloud_url is None or project_id is None or ansible_tower_url is None or ansible_tower_token is None:
    raise Exception("Ensure you copied the .env file that you created earlier into the same directory as this script")
else:
    creds = {
        "url": ibm_cloud_url,
        "apikey": api_key 
    }

# Read the CSV file and get system matches
def read_default_systems(csv_path, environment):
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"The file {csv_path} does not exist.")
    df = pd.read_csv(csv_path)
    matches = df[df.apply(lambda row: environment.lower() in row.astype(str).str.lower().values, axis=1)]
    return matches

# Function to send prompts to WatsonxAI
def send_to_watsonxai(prompts,
                      model_name="ibm/granite-13b-chat-v2",
                      decoding_method="greedy",
                      max_new_tokens=100,
                      min_new_tokens=30,
                      temperature=1.0,
                      repetition_penalty=1.0):
    assert not any(map(lambda prompt: len(prompt) < 1, prompts)), "Make sure none of the prompts in the inputs prompts are empty"
    
    model_params = {
        GenParams.DECODING_METHOD: decoding_method,
        GenParams.MIN_NEW_TOKENS: min_new_tokens,
        GenParams.MAX_NEW_TOKENS: max_new_tokens,
        GenParams.RANDOM_SEED: 42,
        GenParams.TEMPERATURE: temperature,
        GenParams.REPETITION_PENALTY: repetition_penalty,
    }
    
    model = Model(
        model_id=model_name,
        params=model_params,
        credentials=creds,
        project_id=project_id
    )

    responses = []
    for prompt in prompts:
        response = model.generate_text(prompt)
        responses.append(response)
    return responses

# Function to execute the Ansible playbook
def ansible_playbook(host, entity_type, entity_name):
    try:
        headers = {
            'Authorization': f'Bearer {ansible_tower_token}',
            'Content-Type': 'application/json'
        }

        payload = {
            'extra_vars': {
                'host': host,
                'entity_type': entity_type,
                'entity_name': entity_name
            }
        }

        print("Payload sent to Ansible Tower:")
        print(payload)

        response = requests.post(f'{ansible_tower_url}/api/v2/job_templates/5097/launch/', headers=headers, json=payload, verify=False)
        response.raise_for_status()
        job_id = response.json().get('id')
        
        # Poll the job status
        while True:
            job_response = requests.get(f'{ansible_tower_url}/api/v2/jobs/{job_id}/', headers=headers, verify=False)
            job_response.raise_for_status()
            job_status = job_response.json().get('status')
            if job_status in ['successful', 'failed']:
                break
            print("Working on the query, please wait...")
            time.sleep(10)  # Wait 10 seconds before the next check

        # Ensure the job is completely done
        time.sleep(15)  # Extra wait time to ensure all events are logged

        # Get the job results
        if job_status == 'successful':
            job_output_url = f'{ansible_tower_url}/api/v2/jobs/{job_id}/stdout/?format=html'
            job_output_response = requests.get(job_output_url, headers=headers, verify=False)
            job_output_response.raise_for_status()
            full_output_html = job_output_response.text

            # Clean the HTML content to extract only the relevant text
            soup = BeautifulSoup(full_output_html, 'html.parser')
            full_output = soup.get_text(separator="\n")

            # Extract the relevant output for "TASK [Print RACF output parts]"
            start_marker = "TASK [Print RACF output parts]"
            end_marker = "PLAY RECAP"
            start_index = full_output.find(start_marker)
            end_index = full_output.find(end_marker)

            if start_index != -1 and end_index != -1:
                relevant_output = full_output[start_index:end_index]
                
                # Clean up the extracted output
                cleaned_output = []
                for line in relevant_output.split('\n'):
                    if "msg" in line:
                        cleaned_line = line.split("msg")[1].strip(": ").strip("\"")
                        cleaned_output.append(cleaned_line)
                
                cleaned_output_str = "\n".join(cleaned_output)

                # Save the cleaned output to a text file
                output_file_path = os.path.join(os.getcwd(), 'ansible_output.txt')
                with open(output_file_path, 'w') as f:
                    f.write(cleaned_output_str)

                print(f"Cleaned Ansible output saved to {output_file_path}")
                return output_file_path
            else:
                print("No relevant output found in the Ansible output.")
                return None
        else:
            print("The playbook execution failed.")
            return None
    
    except Exception as e:
        print(f"An error occurred while executing the playbook: {str(e)}")
        return None

# Function to process and send the racf_output information to Watsonx for humanization
def process_and_humanize_racf_output(file_path, entity_type, entity_name):
    try:
        with open(file_path, 'r') as f:
            relevant_output = f.read()

        # Debugging prompt
        prompt = f"The following information is about a user from a z/OS server. Please provide this information to the user in a natural language format: {relevant_output}"
        print(f"Debug Prompt: {prompt}")

        # Send the relevant output to Watsonx
        responses = send_to_watsonxai([prompt])
        humanized_response = responses[0]
        print(f"Humanized information for {entity_type} {entity_name}:")
        print(humanized_response)
    
    except Exception as e:
        print(f"An error occurred while processing the Ansible output file: {str(e)}")

# Function to handle chatbot interaction in a loop
def chatbot_interaction():
    while True:
        try:
            print("\nWelcome to the z/OS User and Group Information Chatbot")
            entity_type = input("Do you want information about a 'user' or a 'group'? (Type 'exit' to terminate): ").strip().lower()
            if entity_type == 'exit':
                print("Thank you for using the chatbot. Goodbye!")
                break
            if entity_type not in ['user', 'group']:
                print("Invalid input. Please select 'user' or 'group'.")
                continue

            environment = input("Please provide the environment (e.g., server XYZ) or type 'info' to get all information: ").strip()
            
            # Read the CSV file
            csv_path = 'default_systems.csv'  # Use a relative path
            matches = read_default_systems(csv_path, environment)
            
            if matches.empty:
                print(f"The environment {environment} is not in the system list.")
                continue

            # If user requests information about the environment
            if environment.lower() == 'info':
                for index, row in matches.iterrows():
                    print(f"System: {row['system']}, Host: {row['host']}")
                continue

            # Show matches and ask the user
            host = None
            for index, row in matches.iterrows():
                system_name = row['system']
                host = row['host']
                confirmation = input(f"Do you mean '{system_name}'? (yes/no): ").strip().lower()
                if confirmation == "yes":
                    environment = system_name
                    break

            if host is None:
                print("No host was found for the provided environment.")
                continue

            # Ask for the user or group name
            entity_name = input(f"Please provide the {entity_type} name: ").strip()

            # Send the variables to Ansible
            print("Querying the information, this may take a few moments...")
            start_time = time.time()
            output_file_path = ansible_playbook(host, entity_type, entity_name)
            end_time = time.time()

            elapsed_time = end_time - start_time
            print(f"Query completed in approximately {elapsed_time:.2f} seconds.")

            # Process and humanize the racf_output information
            if output_file_path:
                print(f"Ansible output file found: {output_file_path}")
                process_and_humanize_racf_output(output_file_path, entity_type, entity_name)
            else:
                print("No information was obtained from the z/OS server.")
        
        except Exception as e:
            print(f"An error occurred: {str(e)}")

# Execute the chatbot interaction
if __name__ == "__main__":
    chatbot_interaction()
