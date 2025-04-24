from flask import Flask, render_template, request, send_file, redirect, url_for, jsonify
import subprocess
import json
import os
import tempfile
import time
import threading
import requests
from datetime import datetime

app = Flask(__name__)

# Add Docker path to the environment
os.environ["PATH"] = "/run/current-system/sw/bin:" + os.environ.get("PATH", "")

# Storage for background tasks
tasks = {}

def run_docker_info(task_id, use_ollama):
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
        with open(json_file, 'w') as f:
            f.write("[")
            for i, container_id in enumerate(containers):
                if container_id:  # Skip empty lines
                    if i > 0:
                        f.write(",")
                    container_info = subprocess.check_output(["docker", "inspect", container_id]).decode()
                    f.write(container_info)
            f.write("]")
        
        # Update task status
        tasks[task_id]['status'] = 'generating'
        tasks[task_id]['message'] = 'Generating markdown report...'
        
        # Generate markdown report
        if use_ollama:
            # Check if Ollama API is accessible
            try:
                model = "gemma3:latest"
                model_check = requests.get("http://localhost:11434/api/tags", timeout=5).json()
                
                # Check if the model exists
                model_found = False
                available_models = []
                
                if 'models' in model_check:
                    available_models = [m['name'] for m in model_check['models']]
                    if model in available_models:
                        model_found = True
                    # If specific model not found, check for any deepseek model
                    elif any("deepseek" in m for m in available_models):
                        model = next(m for m in available_models if "deepseek" in m)
                        model_found = True
                
                if model_found:
                    # Create prompt
                    with open(json_file, 'r') as f:
                        json_data = f.read()
                    
                    prompt = f"""Create a comprehensive markdown report about Docker containers from the following JSON data. 
Include sections for:
1. Executive Summary (count of containers, images used, etc.)
2. Detailed Container Information (organized by container)
3. Network Configuration
4. Volume Mounts
5. Resource Usage and Limits
6. Environment Variables
7. Health Checks
8. Security Profile

Format the markdown to be well-structured with proper headings, tables, and code blocks where appropriate.

JSON data:
{json_data}"""
                    
                    # Make request to Ollama API
                    response = requests.post(
                        "http://localhost:11434/api/generate",
                        json={"model": model, "prompt": prompt, "stream": False},
                        timeout=180  # Longer timeout for large model responses
                    )
                    
                    # Write response to markdown file
                    if response.status_code == 200:
                        with open(markdown_file, 'w') as f:
                            f.write(response.json().get('response', ''))
                        tasks[task_id]['status'] = 'completed'
                        tasks[task_id]['message'] = 'Report generated successfully using Ollama.'
                        tasks[task_id]['file_path'] = markdown_file
                        return
                    else:
                        # If API call fails, fall back to basic report
                        tasks[task_id]['message'] = f'Ollama API call failed with status {response.status_code}. Falling back to basic report.'
                else:
                    tasks[task_id]['message'] = f'Ollama model not found. Available models: {", ".join(available_models)}. Falling back to basic report.'
            
            except (requests.RequestException, json.JSONDecodeError) as e:
                tasks[task_id]['message'] = f'Error accessing Ollama API: {str(e)}. Falling back to basic report.'
        
        # If we got here, either Ollama was not requested or it failed
        # Generate basic markdown report
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
    
    # Check if Ollama API is available
    try:
        requests.get("http://localhost:11434/api/tags", timeout=2)
        ollama_available = True
    except requests.RequestException:
        ollama_available = False
    
    return render_template('index.html', 
                          docker_available=docker_available,
                          ollama_available=ollama_available)


@app.route('/generate', methods=['POST'])
def generate_report():
    """Start the report generation process"""
    use_ollama = request.form.get('use_ollama') == 'true'
    
    # Create a task ID and initialize task
    task_id = str(int(time.time()))
    tasks[task_id] = {
        'status': 'starting',
        'message': 'Initializing task...',
        'use_ollama': use_ollama,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # Start background task
    thread = threading.Thread(target=run_docker_info, args=(task_id, use_ollama))
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
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    
    # Create the template files
    with open('templates/index.html', 'w') as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Docker Container Info</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .container { max-width: 800px; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4">Docker Container Information Tool</h1>
        
        {% if not docker_available %}
        <div class="alert alert-danger">
            <strong>Error:</strong> Docker is not available. Please ensure Docker is installed and running.
        </div>
        {% else %}
        <div class="card">
            <div class="card-body">
                <h5 class="card-title">Generate Docker Container Report</h5>
                <p class="card-text">This tool will collect information about all running Docker containers and generate a comprehensive markdown report.</p>
                
                <form action="/generate" method="post">
                    {% if ollama_available %}
                    <div class="form-check mb-3">
                        <input class="form-check-input" type="checkbox" id="use_ollama" name="use_ollama" value="true">
                        <label class="form-check-label" for="use_ollama">
                            Use Ollama LLM for enhanced report generation
                        </label>
                        <div class="form-text">Ollama API detected on port 11434. Using Ollama will create a more detailed and well-formatted report.</div>
                    </div>
                    {% else %}
                    <div class="alert alert-warning">
                        Ollama API not detected on port 11434. Basic report generation will be used.
                    </div>
                    {% endif %}
                    
                    <button type="submit" class="btn btn-primary">Generate Report</button>
                </form>
            </div>
        </div>
        {% endif %}
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>""")
    
    with open('templates/status.html', 'w') as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Task Status - Docker Container Info</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .container { max-width: 800px; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4">Task Status</h1>
        
        <div class="card mb-4">
            <div class="card-body">
                <h5 class="card-title">Task: {{ task_id }}</h5>
                <p><strong>Started:</strong> {{ task.timestamp }}</p>
                <p><strong>Status:</strong> <span id="status-text">{{ task.status }}</span></p>
                <p><strong>Message:</strong> <span id="message-text">{{ task.message }}</span></p>
                
                <div class="progress mb-3">
                    <div id="progress-bar" class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width: 0%"></div>
                </div>
                
                <div id="completed-actions" style="display: none;">
                    <a href="/download/{{ task_id }}" class="btn btn-success">Download Report</a>
                    <a href="/view/{{ task_id }}" class="btn btn-primary">View Report</a>
                </div>
                
                <div id="error-actions" style="display: none;">
                    <a href="/" class="btn btn-primary">Back to Home</a>
                </div>
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const statusText = document.getElementById('status-text');
        const messageText = document.getElementById('message-text');
        const progressBar = document.getElementById('progress-bar');
        const completedActions = document.getElementById('completed-actions');
        const errorActions = document.getElementById('error-actions');
        
        function updateProgress(status) {
            switch(status) {
                case 'starting':
                    progressBar.style.width = '10%';
                    break;
                case 'collecting':
                    progressBar.style.width = '30%';
                    break;
                case 'generating':
                    progressBar.style.width = '70%';
                    break;
                case 'completed':
                    progressBar.style.width = '100%';
                    progressBar.classList.remove('progress-bar-animated');
                    progressBar.classList.remove('progress-bar-striped');
                    progressBar.classList.add('bg-success');
                    completedActions.style.display = 'block';
                    break;
                case 'error':
                    progressBar.style.width = '100%';
                    progressBar.classList.remove('progress-bar-animated');
                    progressBar.classList.remove('progress-bar-striped');
                    progressBar.classList.add('bg-danger');
                    errorActions.style.display = 'block';
                    break;
            }
        }
        
        function checkStatus() {
            fetch('/api/status/{{ task_id }}')
                .then(response => response.json())
                .then(data => {
                    statusText.textContent = data.status;
                    messageText.textContent = data.message;
                    updateProgress(data.status);
                    
                    if (data.status !== 'completed' && data.status !== 'error') {
                        setTimeout(checkStatus, 1000);
                    }
                })
                .catch(error => {
                    console.error('Error checking status:', error);
                    setTimeout(checkStatus, 5000);
                });
        }
        
        // Initial update
        updateProgress('{{ task.status }}');
        
        // Start status checks
        if ('{{ task.status }}' !== 'completed' && '{{ task.status }}' !== 'error') {
            setTimeout(checkStatus, 1000);
        } else if ('{{ task.status }}' === 'completed') {
            completedActions.style.display = 'block';
        } else if ('{{ task.status }}' === 'error') {
            errorActions.style.display = 'block';
        }
    </script>
</body>
</html>""")
    
    with open('templates/view.html', 'w') as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>View Report - Docker Container Info</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.1.0/github-markdown.min.css">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        body { padding: 20px; }
        .container { max-width: 1000px; }
        .markdown-body { padding: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>Docker Container Report</h1>
            <div>
                <a href="/download/{{ task_id }}" class="btn btn-success">Download</a>
                <a href="/" class="btn btn-secondary">Back to Home</a>
            </div>
        </div>
        
        <div class="card">
            <div class="card-body markdown-body" id="markdown-content">
                <!-- Content will be rendered here -->
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Render markdown
        document.getElementById('markdown-content').innerHTML = marked.parse(`{{ content|safe }}`);
    </script>
</body>
</html>""")
    
    print("Starting Flask app on http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
