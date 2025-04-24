from flask import Flask, render_template, request, send_file, redirect, url_for, jsonify
import subprocess
import json
import os
import tempfile
import time
import threading
import requests
import openai # Import OpenAI library
from datetime import datetime

app = Flask(__name__)

# Add Docker path to the environment
os.environ["PATH"] = "/run/current-system/sw/bin:" + os.environ.get("PATH", "")

# Storage for background tasks
tasks = {}

# Configuration
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") # Get API key from environment
OPENAI_MODEL = "gpt-4.1-nano" # Specify the desired OpenAI model

def run_docker_info(task_id, use_openai):
    """Run the Docker info collection and report generation in the background"""
    try:
        # Create temp directory for this task
        task_dir = tempfile.mkdtemp(prefix=f"docker_info_{task_id}_")
        json_file = os.path.join(task_dir, "containers_info.json")
        markdown_file = os.path.join(task_dir, "docker_containers_info.md")
        
        # Update task status
        tasks[task_id]['status'] = 'collecting'
        tasks[task_id]['message'] = 'Collecting Docker container information...'
        
        # Get all running containers
        containers = subprocess.check_output(["docker", "ps", "--format", "{{.ID}}"]).decode().strip().split('\n')
        if not containers or containers == ['']:
            tasks[task_id]['status'] = 'error'
            tasks[task_id]['message'] = 'No running containers found.'
            return

        # Collect information for each container
        all_container_info = []
        for container_id in containers:
            if container_id:  # Skip empty lines
                try:
                    # docker inspect outputs a JSON array with one element
                    inspect_output = subprocess.check_output(["docker", "inspect", container_id]).decode()
                    container_info_list = json.loads(inspect_output)
                    if container_info_list: # Check if the list is not empty
                        all_container_info.append(container_info_list[0]) # Append the actual object
                except (subprocess.SubprocessError, json.JSONDecodeError) as inspect_error:
                    tasks[task_id]['status'] = 'error'
                    tasks[task_id]['message'] = f"Error inspecting container {container_id}: {str(inspect_error)}"
                    return # Stop processing if inspection fails for one container

        # Write the collected info as a proper JSON array
        try:
            with open(json_file, 'w') as f:
                json.dump(all_container_info, f, indent=2) # Use indent for readability (optional)
        except IOError as write_error:
             tasks[task_id]['status'] = 'error'
             tasks[task_id]['message'] = f"Error writing container info to file: {str(write_error)}"
             return

        # Update task status
        tasks[task_id]['status'] = 'generating'
        tasks[task_id]['message'] = 'Generating markdown report...'

        # Generate markdown report
        if use_openai:
            if not OPENAI_API_KEY:
                tasks[task_id]['message'] = 'OpenAI API key not configured. Falling back to basic report.'
            else:
                try:
                    # Initialize OpenAI client
                    client = openai.OpenAI(api_key=OPENAI_API_KEY)

                    # Create prompt
                    with open(json_file, 'r') as f:
                        json_data = f.read()

                    system_prompt = "You are an expert assistant specialized in analyzing Docker container configurations and generating clear, concise markdown reports."
                    user_prompt = f"""Create a comprehensive markdown report about Docker containers from the following JSON data.
Include sections for:
1. Executive Summary (count of containers, unique images used, common networks, etc.)
2. Detailed Container Information:
   Present this information in a markdown table with the following columns:
   | Name | Short ID | Image | Status | Ports | Networks | Mounts |
   |------|----------|-------|--------|-------|----------|--------|
   Use the first 12 characters for the 'Short ID'. For the 'Ports', 'Networks', and 'Mounts' columns, summarize the information concisely. Use backticks (`) around complex entries if needed to prevent breaking the table structure (e.g., `port1 -> host:port1, port2 -> host:port2`).
3. Network Configuration Summary (List networks and connected containers)
4. Volume Mounts Summary (List volumes/bind mounts and containers using them)
5. Resource Usage and Limits (If available in data)
6. Environment Variables (Mention sensitive variables should be handled carefully, list non-sensitive ones if appropriate, or just summarize their presence)
7. Health Checks (If configured)
8. Security Considerations (Based on exposed ports, capabilities, user, etc. - provide general advice)

Format the markdown to be well-structured with proper headings, tables (for structured data like ports/mounts), and code blocks where appropriate. Focus on clarity and readability.

JSON data:
```json
{json_data}
```"""

                    # Make request to OpenAI API
                    completion = client.chat.completions.create(
                        model=OPENAI_MODEL,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.5, # Adjust creativity/factuality
                        timeout=180
                    )

                    # Extract response and write to file
                    if completion.choices and completion.choices[0].message:
                        report_content = completion.choices[0].message.content
                        with open(markdown_file, 'w') as f:
                            f.write(report_content)
                        tasks[task_id]['status'] = 'completed'
                        tasks[task_id]['message'] = f'Report generated successfully using OpenAI ({OPENAI_MODEL}).'
                        tasks[task_id]['file_path'] = markdown_file
                        return
                    else:
                        tasks[task_id]['message'] = 'OpenAI API returned an empty response. Falling back to basic report.'

                except openai.APIError as e:
                    tasks[task_id]['message'] = f"OpenAI API Error: {e}. Falling back to basic report."
                except openai.AuthenticationError:
                     tasks[task_id]['message'] = f"OpenAI Authentication Error (check API key). Falling back to basic report."
                except openai.RateLimitError:
                     tasks[task_id]['message'] = f"OpenAI Rate Limit Exceeded. Falling back to basic report."
                except Exception as e: # Catch other potential errors (network, etc.)
                    tasks[task_id]['message'] = f'Error during OpenAI report generation: {str(e)}. Falling back to basic report.'

        # If we got here, either OpenAI was not requested or it failed
        # Generate basic markdown report
        tasks[task_id]['message'] = tasks[task_id].get('message', '') + ' Generating basic report...' # Append to existing message if fallback occurred
        with open(markdown_file, 'w') as f:
            f.write(f"# Docker Containers Report\n")
            f.write(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n\n")
            
            f.write("## Summary\n")
            f.write(f"Total running containers: {len([c for c in containers if c])}\n\n")
            
            f.write("## Container Details\n\n")
            
            for container_id in containers:
                if container_id:  # Skip empty lines
                    # Get container info
                    name = subprocess.check_output(["docker", "inspect", "--format", "{{.Name}}", container_id]).decode().strip().replace('/', '')
                    image = subprocess.check_output(["docker", "inspect", "--format", "{{.Config.Image}}", container_id]).decode().strip()
                    created = subprocess.check_output(["docker", "inspect", "--format", "{{.Created}}", container_id]).decode().strip()
                    status = subprocess.check_output(["docker", "inspect", "--format", "{{.State.Status}}", container_id]).decode().strip()
                    ports = subprocess.check_output(["docker", "inspect", "--format", "{{range $p, $conf := .NetworkSettings.Ports}}{{$p}} -> {{range $conf}}{{.HostIp}}:{{.HostPort}}{{end}}{{end}}", container_id]).decode().strip()
                    networks = subprocess.check_output(["docker", "inspect", "--format", "{{range $net, $conf := .NetworkSettings.Networks}}{{$net}} {{end}}", container_id]).decode().strip()
                    
                    f.write(f"### Container: {name} ({container_id})\n")
                    f.write(f"- **Image**: {image}\n")
                    f.write(f"- **Created**: {created}\n")
                    f.write(f"- **Status**: {status}\n")
                    f.write(f"- **Ports**: {ports}\n")
                    f.write(f"- **Networks**: {networks}\n\n")
                    
                    # Get mounted volumes
                    f.write("#### Volumes\n")
                    mounts = subprocess.check_output(["docker", "inspect", "--format", "{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Type}}){{println}}{{end}}", container_id]).decode().strip()
                    
                    if not mounts:
                        f.write("No volumes mounted.\n\n")
                    else:
                        f.write("```\n")
                        f.write(mounts)
                        f.write("\n```\n\n")
                    
                    # Get environment variables
                    f.write("#### Environment Variables\n")
                    env_vars = subprocess.check_output(["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container_id]).decode().strip()
                    
                    if not env_vars:
                        f.write("No environment variables set.\n\n")
                    else:
                        f.write("```\n")
                        f.write(env_vars)
                        f.write("\n```\n\n")
                    
                    # Get resource limits
                    f.write("#### Resource Limits\n")
                    cpu_shares = subprocess.check_output(["docker", "inspect", "--format", "{{.HostConfig.CpuShares}}", container_id]).decode().strip()
                    memory = subprocess.check_output(["docker", "inspect", "--format", "{{.HostConfig.Memory}}", container_id]).decode().strip()
                    
                    f.write(f"- CPU Shares: {cpu_shares}\n")
                    f.write(f"- Memory Limit: {memory} bytes\n\n")
        
        # Update task status
        tasks[task_id]['status'] = 'completed'
        tasks[task_id]['message'] = 'Report generated successfully.'
        tasks[task_id]['file_path'] = markdown_file
        
    except Exception as e:
        tasks[task_id]['status'] = 'error'
        tasks[task_id]['message'] = f"Error: {str(e)}"


@app.route('/')
def index():
    """Main page with form to generate report"""
    # Check if Docker is installed
    try:
        subprocess.check_output(["docker", "--version"])
        docker_available = True
    except (subprocess.SubprocessError, FileNotFoundError):
        docker_available = False

    # Check if OpenAI API key is configured
    openai_available = bool(OPENAI_API_KEY)

    return render_template('index.html',
                          docker_available=docker_available,
                          openai_available=openai_available) # Pass OpenAI availability


@app.route('/generate', methods=['POST'])
def generate_report():
    """Start the report generation process"""
    use_openai = request.form.get('use_openai') == 'true' # Check for use_openai

    # Create a task ID and initialize task
    task_id = str(int(time.time()))
    tasks[task_id] = {
        'status': 'starting',
        'message': 'Initializing task...',
        'use_openai': use_openai, # Store use_openai flag
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    # Start background task
    thread = threading.Thread(target=run_docker_info, args=(task_id, use_openai)) # Pass use_openai
    thread.daemon = True
    thread.start()
    
    return redirect(url_for('task_status', task_id=task_id))


@app.route('/status/<task_id>')
def task_status(task_id):
    """Show status of a task"""
    if task_id not in tasks:
        return "Task not found", 404
    
    return render_template('status.html', task_id=task_id, task=tasks[task_id])


@app.route('/api/status/<task_id>')
def api_task_status(task_id):
    """API endpoint to get task status"""
    if task_id not in tasks:
        return jsonify({'error': 'Task not found'}), 404
    
    return jsonify(tasks[task_id])


@app.route('/download/<task_id>')
def download_report(task_id):
    """Download the generated report"""
    if task_id not in tasks or tasks[task_id]['status'] != 'completed':
        return "Report not available", 404
    
    file_path = tasks[task_id]['file_path']
    return send_file(file_path, as_attachment=True, download_name='docker_containers_info.md')


@app.route('/view/<task_id>')
def view_report(task_id):
    """View the generated report in the browser"""
    if task_id not in tasks or tasks[task_id]['status'] != 'completed':
        return "Report not available", 404
    
    try:
        with open(tasks[task_id]['file_path'], 'r') as f:
            content = f.read()
        return render_template('view.html', content=content, task_id=task_id)
    except Exception as e:
        return f"Error reading report: {str(e)}", 500


# Templates directory will be automatically used by Flask

if __name__ == '__main__':
    # Ensure templates directory exists (optional, Flask finds it by convention)
    # os.makedirs('templates', exist_ok=True) 
    
    print("Starting Flask app on http://127.0.0.1:5010") # Changed default port for clarity
    app.run(host='0.0.0.0', port=5010, debug=True)
