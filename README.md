# Docker Container Information Tool

A web application built with Flask that collects information about running Docker containers and generates a comprehensive markdown report. It can optionally use Ollama for enhanced report generation.

## Features

-   Collects detailed information about all running Docker containers using `docker inspect`.
-   Generates a markdown report summarizing container details (name, image, status, ports, networks, volumes, environment variables, resource limits).
-   Optionally uses OpenAI's `gpt-4.1-nano` model (if API key is provided) to generate a more detailed and structured report.
-   Provides a web interface to trigger report generation, view status, download, and view the report.
-   Includes API endpoints for status checking.

## Setup

1.  **Prerequisites:**
    *   Python 3.x
    *   Docker installed and running.
    *   (Optional) OpenAI API Key for enhanced reports.

2.  **Clone the repository:**
    ```bash
    git clone <your-repository-url>
    cd <repository-directory>
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **(Optional) Set OpenAI API Key:**
    For enhanced report generation using OpenAI, set the `OPENAI_API_KEY` environment variable:
    ```bash
    export OPENAI_API_KEY='your-api-key-here' 
    ```
    *Note: On Windows, use `set OPENAI_API_KEY=your-api-key-here` or set it via system properties.*

## Usage

1.  **Run the Flask application:**
    ```bash
    python app.py
    ```
    The application will start on `http://127.0.0.1:5000` by default.

2.  **Access the web interface:**
    Open your web browser and navigate to `http://127.0.0.1:5000`.

3.  **Generate a report:**
    *   Click the "Generate Report" button.
    *   Optionally, check the "Use OpenAI GPT" box if you have set your API key and want an enhanced report.
    *   You will be redirected to a status page.

4.  **View/Download the report:**
    *   Once the report generation is complete, the status page will provide buttons to "Download Report" or "View Report" directly in the browser.

## API Endpoints

-   `/`: Main page to initiate report generation.
-   `/generate` (POST): Starts the report generation task.
-   `/status/<task_id>`: HTML page showing the status of a specific task.
-   `/api/status/<task_id>`: JSON endpoint to get the status of a specific task.
-   `/download/<task_id>`: Downloads the generated markdown report for a completed task.
-   `/view/<task_id>`: Displays the generated markdown report in the browser for a completed task.

## Project Structure

```
.
├── app.py             # Main Flask application logic
├── requirements.txt   # Python dependencies
├── templates/         # HTML templates
│   ├── index.html     # Main page template
│   ├── status.html    # Task status page template
│   └── view.html      # Report view page template
└── README.md          # This file
```
