from flask import Flask, render_template, request, send_file, redirect, url_for, jsonify, flash
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
app.secret_key = os.urandom(24) # Needed for flashing messages

# --- Helper Functions ---

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
        tasks[task_id]['message'] = 'Generating basic markdown report...' # Start with basic report message

        # --- Basic Report Generation (Always Run First) ---
        compose_projects = {}
        container_details_for_report = []

        # Process collected container info for the basic report
        for container_info in all_container_info:
            container_id = container_info.get('Id', 'N/A')[:12] # Short ID
            name = container_info.get('Name', 'N/A').lstrip('/')
            image = container_info.get('Config', {}).get('Image', 'N/A')
            created = container_info.get('Created', 'N/A')
            status = container_info.get('State', {}).get('Status', 'N/A')

            # Extract ports
            ports_dict = container_info.get('NetworkSettings', {}).get('Ports', {})
            ports_list = []
            for container_port, host_bindings in ports_dict.items():
                if host_bindings:
                    for binding in host_bindings:
                        ports_list.append(f"{container_port} -> {binding.get('HostIp', '0.0.0.0')}:{binding.get('HostPort', 'N/A')}")
                else:
                     ports_list.append(f"{container_port} (no host binding)")
            ports = ", ".join(ports_list) if ports_list else "None"

            # Extract networks
            networks_dict = container_info.get('NetworkSettings', {}).get('Networks', {})
            networks = ", ".join(networks_dict.keys()) if networks_dict else "None"

            # Extract mounts
            mounts_list = []
            for mount in container_info.get('Mounts', []):
                mount_str = f"{mount.get('Source', 'N/A')} -> {mount.get('Destination', 'N/A')} ({mount.get('Type', 'N/A')}"
                if not mount.get('RW', True):
                    mount_str += ", ro"
                mount_str += ")"
                mounts_list.append(mount_str)
            mounts_str = "\n".join(mounts_list) if mounts_list else "No volumes mounted."

            # Extract environment variables (excluding potentially sensitive ones is safer for basic report)
            env_vars = container_info.get('Config', {}).get('Env', [])
            env_vars_str = "\n".join(env_vars) if env_vars else "No environment variables set."
            # Consider filtering sensitive vars here if needed

            # Extract resource limits
            host_config = container_info.get('HostConfig', {})
            cpu_shares = host_config.get('CpuShares', 'N/A')
            memory_limit = host_config.get('Memory', 0) # In bytes
            memory_limit_str = f"{memory_limit / (1024*1024):.2f} MiB" if memory_limit else "N/A"

            # Store details for report generation
            container_details_for_report.append({
                'id': container_id,
                'name': name,
                'image': image,
                'created': created,
                'status': status,
                'ports': ports,
                'networks': networks,
                'mounts': mounts_str,
                'env_vars': env_vars_str,
                'cpu_shares': cpu_shares,
                'memory_limit': memory_limit_str
            })

            # Check for Docker Compose label
            labels = container_info.get('Config', {}).get('Labels', {})
            project_name = labels.get('com.docker.compose.project')
            if project_name:
                if project_name not in compose_projects:
                    compose_projects[project_name] = []
                compose_projects[project_name].append(f"{name} ({container_id})")


        # Write the basic markdown report (always, using 'w' mode)
        try:
            with open(markdown_file, 'w') as f:
                f.write(f"# Docker Containers Report (Basic)\n")
                f.write(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n\n")

                f.write("## Summary\n")
                f.write(f"Total running containers: {len(all_container_info)}\n\n")

                # Add Docker Compose Projects section if any were found
                if compose_projects:
                    f.write("## Docker Compose Projects\n\n")
                    for project, project_containers in compose_projects.items():
                        f.write(f"### Project: {project}\n")
                        for container_name_id in project_containers:
                            f.write(f"- {container_name_id}\n")
                        f.write("\n")

                f.write("## Container Details\n\n")

                for details in container_details_for_report:
                    f.write(f"### Container: {details['name']} ({details['id']})\n")
                    f.write(f"- **Image**: {details['image']}\n")
                    f.write(f"- **Created**: {details['created']}\n")
                    f.write(f"- **Status**: {details['status']}\n")
                    f.write(f"- **Ports**: {details['ports']}\n")
                    f.write(f"- **Networks**: {details['networks']}\n\n")

                    f.write("#### Volumes\n")
                    f.write("```\n")
                    f.write(details['mounts'])
                    f.write("\n```\n\n")

                    f.write("#### Environment Variables\n")
                    f.write("```\n")
                    f.write(details['env_vars'])
                    f.write("\n```\n\n")

                    f.write("#### Resource Limits\n")
                    f.write(f"- CPU Shares: {details['cpu_shares']}\n")
                    f.write(f"- Memory Limit: {details['memory_limit']}\n\n")

            # Basic report generated, update status before potentially trying AI
            tasks[task_id]['status'] = 'completed' # Tentative status
            tasks[task_id]['message'] = 'Basic report generated successfully.'
            tasks[task_id]['file_path'] = markdown_file

        except IOError as write_error:
             tasks[task_id]['status'] = 'error'
             tasks[task_id]['message'] = f"Error writing basic report to file: {str(write_error)}"
             return # Stop if basic report writing fails

        # --- AI Enhanced Report Generation (Optional) ---
        if use_openai:
            tasks[task_id]['status'] = 'generating_ai' # More specific status
            tasks[task_id]['message'] = 'Basic report generated. Now generating AI enhanced report...'

            if not OPENAI_API_KEY:
                tasks[task_id]['status'] = 'completed' # Revert to completed as AI cannot run
                tasks[task_id]['message'] = 'Basic report generated. OpenAI API key not configured, skipping AI enhancement.'
                return # Return here as AI part is skipped
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

                    # Extract response and append to file
                    if completion.choices and completion.choices[0].message:
                        report_content = completion.choices[0].message.content
                        try:
                            with open(markdown_file, 'a') as f: # Open in append mode
                                f.write("\n\n---\n\n# AI Enhanced Report\n\n") # Add separator
                                f.write(report_content)
                            tasks[task_id]['status'] = 'completed'
                            tasks[task_id]['message'] = f'Basic report and AI enhanced report ({OPENAI_MODEL}) generated successfully.'
                            # file_path is already set
                            return # Task fully completed
                        except IOError as append_error:
                            tasks[task_id]['status'] = 'error' # Error during append
                            tasks[task_id]['message'] = f'Basic report generated, but failed to append AI report: {str(append_error)}'
                            return
                    else:
                        # AI returned empty response, but basic report is done
                        tasks[task_id]['status'] = 'completed'
                        tasks[task_id]['message'] = 'Basic report generated. OpenAI API returned an empty response for enhancement.'
                        return

                except openai.APIError as e:
                    error_msg = f"OpenAI API Error: {e}"
                except openai.AuthenticationError:
                     error_msg = f"OpenAI Authentication Error (check API key)"
                except openai.RateLimitError:
                     error_msg = f"OpenAI Rate Limit Exceeded"
                except Exception as e: # Catch other potential errors (network, etc.)
                    error_msg = f'Error during OpenAI report generation: {str(e)}'

                # If any OpenAI exception occurred, update status but basic report is still available
                tasks[task_id]['status'] = 'completed' # Mark as completed as basic report exists
                tasks[task_id]['message'] = f'Basic report generated. AI enhancement failed: {error_msg}.'
                return # Return as AI part failed

        # If use_openai was false, the function implicitly returns here
        # as the basic report generation already set status to 'completed'.

    except Exception as e:
        # Catch-all for errors before or during basic report generation
        tasks[task_id]['status'] = 'error'
        tasks[task_id]['message'] = f"Error: {str(e)}"

def get_containers(show_all=False):
    """Gets a list of Docker containers with parsed port info."""
    cmd = ["docker", "ps", "--format", "{{json .}}"]
    if show_all:
        cmd.append("-a")

    containers = []

    def parse_ports(ports_str):
        """Parses the port string from docker ps into structured data."""
        parsed = []
        if not ports_str:
            return parsed
        
        # Example: "0.0.0.0:8080->80/tcp, :::8080->80/tcp, 6379/tcp"
        parts = ports_str.split(',')
        for part in parts:
            part = part.strip()
            port_info = {'host_ip': None, 'host_port': None, 'container_port': None, 'protocol': None, 'link': None}
            
            if '->' in part: # Host binding exists
                host_part, container_part = part.split('->')
                
                # Extract container port/protocol
                if '/' in container_part:
                    port_info['container_port'], port_info['protocol'] = container_part.split('/')
                else:
                    port_info['container_port'] = container_part # Protocol might be missing? Default?
                    port_info['protocol'] = 'tcp' # Assume tcp if missing

                # Extract host IP/port
                ip_port_match = host_part.split(':')
                if len(ip_port_match) == 2: # Format like 0.0.0.0:8080 or :::8080
                     port_info['host_ip'] = ip_port_match[0]
                     port_info['host_port'] = ip_port_match[1]
                     # Create link
                     host_link_ip = 'localhost' if port_info['host_ip'] in ['0.0.0.0', '::'] else port_info['host_ip']
                     port_info['link'] = f"http://{host_link_ip}:{port_info['host_port']}"
                else: # Should not happen with standard docker ps output?
                    print(f"Warning: Unexpected host port format: {host_part}")
                    port_info['host_port'] = host_part # Fallback

            else: # Only container port exposed (e.g., "6379/tcp")
                 if '/' in part:
                    port_info['container_port'], port_info['protocol'] = part.split('/')
                 else:
                    port_info['container_port'] = part
                    port_info['protocol'] = 'tcp' # Assume tcp

            parsed.append(port_info)
        return parsed
    try:
        output = subprocess.check_output(cmd).decode().strip()
        if not output:
            return [] # No containers found
        
        # Each line is a JSON object
        for line in output.splitlines():
            try:
                container_data = json.loads(line)
                # Simplify data for frontend
                containers.append({
                    'id': container_data.get('ID'),
                    'name': container_data.get('Names'),
                    'image': container_data.get('Image'),
                    'status': container_data.get('Status'),
                    'state': container_data.get('State'), # e.g., 'running', 'exited',
                    'ports_raw': container_data.get('Ports', ''), # Get the raw port string
                    'ports_parsed': parse_ports(container_data.get('Ports', '')) # Add parsed ports
                })
            except json.JSONDecodeError:
                print(f"Warning: Could not parse JSON line: {line}") # Log parsing errors
                continue # Skip malformed lines
                
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"Error getting containers: {e}") # Log the error
        # Optionally, raise an exception or return an error indicator
        return None # Indicate an error occurred
        
    return containers

# --- Routes ---

@app.route('/')
def index():
    """Main page with form to generate report"""
    # Check if Docker is installed
    try:
        subprocess.check_output(["docker", "--version"])
        docker_available = True
    except (subprocess.SubprocessError, FileNotFoundError):
        docker_available = False
        running_containers = None # Indicate Docker issue
        flash("Docker command not found. Please ensure Docker is installed and in the system PATH.", "danger")
    
    if docker_available:
        running_containers = get_containers(show_all=False)
        if running_containers is None:
             # Error occurred in get_containers
             flash("Failed to fetch container status from Docker.", "danger")


    # Check if OpenAI API key is configured
    openai_available = bool(OPENAI_API_KEY)

    return render_template('index.html',
                           docker_available=docker_available,
                           openai_available=openai_available,
                           containers=running_containers) # Pass initial container list


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

# --- API Routes ---

@app.route('/api/containers')
def api_get_containers():
    """API endpoint to get container list (running or all)"""
    show_all = request.args.get('all', 'false').lower() == 'true'
    
    try:
        subprocess.check_output(["docker", "--version"])
    except (subprocess.SubprocessError, FileNotFoundError):
         return jsonify({"error": "Docker command not found."}), 500
         
    containers = get_containers(show_all=show_all)
    
    if containers is None:
        return jsonify({"error": "Failed to fetch container status from Docker."}), 500
        
    return jsonify(containers)

@app.route('/api/container/<action>/<container_id>', methods=['POST'])
def api_container_action(action, container_id):
    """API endpoint to start or stop a container"""
    if action not in ['start', 'stop']:
        return jsonify({"error": "Invalid action"}), 400

    # Basic validation for container ID (prevent command injection)
    if not container_id or not container_id.isalnum():
         return jsonify({"error": "Invalid container ID"}), 400

    cmd = ["docker", action, container_id]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return jsonify({"success": True, "message": f"Container {container_id} {action}ed successfully."})
    except FileNotFoundError:
        return jsonify({"error": "Docker command not found."}), 500
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip() if e.stderr else f"Docker command failed for {action}."
        return jsonify({"error": f"Failed to {action} container {container_id}: {error_message}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


# Templates directory will be automatically used by Flask

if __name__ == '__main__':
    # Ensure templates directory exists (optional, Flask finds it by convention)
    # os.makedirs('templates', exist_ok=True) 
    
    print("Starting Flask app on http://127.0.0.1:5010") # Changed default port for clarity
    app.run(host='0.0.0.0', port=5010, debug=True)
