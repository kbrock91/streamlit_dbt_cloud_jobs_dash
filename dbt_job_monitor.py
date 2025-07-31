import requests
import os
import json
from datetime import datetime, timedelta, timezone, date
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

# --- Load Environment Variables ---
# Load variables from .env file into environment variables
load_dotenv()

# --- Configuration (from .env file) ---
API_TOKEN = os.getenv("DBT_CLOUD_API_TOKEN")
ACCOUNT_ID = os.getenv("DBT_CLOUD_ACCOUNT_ID")  # Must be a string containing only numbers
BASE_URL = os.getenv("DBT_CLOUD_BASE_URL")  

# print(f"DEBUG: Value loaded for ACCOUNT_ID: {ACCOUNT_ID}") # DEBUG LINE REMOVED

# --- Basic Validation ---
if not API_TOKEN or API_TOKEN == "YOUR_DBT_CLOUD_API_TOKEN_HERE":
    st.error("API_TOKEN not set. Please replace the placeholder in the script.")
    st.stop()
if not ACCOUNT_ID or ACCOUNT_ID == "YOUR_NUMERIC_ACCOUNT_ID_HERE":
     st.error("ACCOUNT_ID not set. Please replace the placeholder in the script.")
     st.stop()
if not ACCOUNT_ID.isdigit():
     st.error(f"ACCOUNT_ID '{ACCOUNT_ID}' is not valid. It must be a string containing only numbers.")
     st.stop()
if not BASE_URL:
    st.error("BASE_URL not set or empty in the script.")
    st.stop()


print(f"Using Account ID: {ACCOUNT_ID}")
print(f"Using Base URL: {BASE_URL}")
print("API Token is set (not printing value).")

# --- Headers for Authentication ---
AUTH_HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json", # Standard for JSON API requests
    "Accept": "application/json",       # Explicitly accept JSON responses
}

print("Script setup complete. Ready for next steps.")

# --- Helper for making API requests ---
def make_dbt_cloud_request(endpoint, params=None):
    """Makes a GET request to the dbt Cloud API, handling basic errors and returning JSON.

    Args:
        endpoint (str): The API endpoint path (e.g., '/api/v2/accounts/123/runs/').
        params (dict, optional): Dictionary of query parameters for the request.

    Returns:
        dict: The JSON response data if successful, None otherwise.
    """
    if not endpoint.startswith('/'):
        endpoint = f'/{endpoint}' # Ensure leading slash

    url = f"{BASE_URL}{endpoint}"
    # Use st.spinner for visual feedback in Streamlit later, but keep print for now
    # print(f"Making API request to: {url} with params: {params}")

    try:
        response = requests.get(
            url,
            headers=AUTH_HEADERS,
            params=params,
            timeout=60 # Add a timeout (in seconds)
        )
        # Raise an HTTPError exception for bad responses (4xx or 5xx)
        response.raise_for_status()

        # Attempt to parse JSON
        return response.json()

    except requests.exceptions.HTTPError as http_err:
        st.error(f"HTTP error occurred: {http_err} - Status Code: {http_err.response.status_code}")
        try:
            # Try to print the JSON error response from dbt Cloud if available
            st.error(f"Response Body: {http_err.response.json()}")
        except json.JSONDecodeError:
            # Otherwise print the raw text
            st.error(f"Response Body: {http_err.response.text}")
        return None # Indicate failure
    except requests.exceptions.RequestException as req_err:
        # Catch other request errors (connection, timeout, etc.)
        st.error(f"Error making API request to {url}: {req_err}")
        return None # Indicate failure
    except json.JSONDecodeError as json_err:
        # Catch errors if the response isn't valid JSON (unexpected)
        st.error(f"Error decoding JSON response from {url}: {json_err}")
        st.error(f"Response Text: {response.text if 'response' in locals() else 'N/A'}")
        return None # Indicate failure

# --- Helper function to fetch ALL items from a paginated endpoint ---
@st.cache_data(ttl=3600) # Cache for 1 hour
def get_all_items(endpoint, limit_per_page=100):
    """Fetches all items from a paginated v2 list endpoint."""
    all_items = []
    offset = 0
    # st.write(f"Fetching all items from {endpoint}...") # Debug message
    page = 1
    while True:
        # print(f"Fetching page {page} for {endpoint} (Offset: {offset})") # Debug
        params = {"limit": limit_per_page, "offset": offset}
        response_data = make_dbt_cloud_request(endpoint, params=params)
        if response_data is None:
            st.error(f"Failed to fetch data from {endpoint} during pagination (page {page}).")
            return None # Indicate failure

        items_page = response_data.get('data', [])
        if not items_page:
            # print(f"No more items found for {endpoint} after page {page-1}.") # Debug
            break # No more items

        all_items.extend(items_page)

        # Check pagination metadata for total count (more robust stop condition)
        total_count = response_data.get('extra', {}).get('pagination', {}).get('total_count', -1)
        current_count_fetched = offset + len(items_page)

        if len(items_page) < limit_per_page:
            # print(f"Fetched less than limit on page {page} for {endpoint}. Assuming end.") # Debug
            break # Fetched less than limit, must be the last page
        if total_count != -1 and current_count_fetched >= total_count:
            # print(f"Fetched total count ({total_count}) for {endpoint}.") # Debug
             break # Fetched all items according to API

        offset += len(items_page)
        page += 1
    # st.write(f"Finished fetching {len(all_items)} items from {endpoint}.") # Debug message
    return all_items

# --- Specific Fetch Functions using the helper ---
@st.cache_data(ttl=3600)
def get_all_jobs():
    """Fetches all job definitions for the account."""
    endpoint = f"/api/v2/accounts/{ACCOUNT_ID}/jobs/"
    return get_all_items(endpoint)

@st.cache_data(ttl=3600)
def get_all_projects():
    """Fetches all projects for the account."""
    endpoint = f"/api/v2/accounts/{ACCOUNT_ID}/projects/"
    return get_all_items(endpoint)

@st.cache_data(ttl=3600)
def get_all_environments():
    """Fetches all environments for the account."""
    endpoint = f"/api/v2/accounts/{ACCOUNT_ID}/environments/"
    return get_all_items(endpoint)

# --- Function to fetch job runs for a specific day ---
# Renamed and modified to take a date argument
@st.cache_data(ttl=60) # Cache results for 1 minute (to catch recent runs faster)
def get_runs_for_day(target_date: date, limit_per_page=100, max_pages_to_check=20):
    """Retrieves dbt Cloud job runs created on target_date (UTC), filtering client-side.

    Fetches runs ordered by newest first and stops when runs before target_date are found.

    Args:
        target_date (date): The date to fetch runs for.
        limit_per_page (int): How many runs to fetch per API request.
        max_pages_to_check (int): Safety limit on the number of pages to fetch.

    Returns:
        list: A list of run dictionaries created on target_date if successful, None otherwise.
    """
    all_runs_today = []
    offset = 0
    endpoint = f"/api/v2/accounts/{ACCOUNT_ID}/runs/"

    # --- Calculate Start and End of Target Day (UTC) ---
    day_start_utc = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    day_end_utc = day_start_utc + timedelta(days=1)

    st.write(f"Fetching recent runs for account {ACCOUNT_ID} and filtering for {target_date.isoformat()} UTC...")

    for page_num in range(max_pages_to_check):
        st.write(f"Fetching page {page_num + 1}/{max_pages_to_check}... (Offset: {offset})")
        params = {
            "limit": limit_per_page,
            "offset": offset,
            "order_by": "-created_at",
        }
        response_data = make_dbt_cloud_request(endpoint, params=params)

        if response_data is None:
            st.warning(f"API request failed during pagination. Returning potentially incomplete list of {len(all_runs_today)} runs found so far.")
            return all_runs_today if all_runs_today else [] # Return list or empty list

        runs_page = response_data.get('data', [])
        if not runs_page:
            st.write("No more runs found in API history.")
            break

        st.write(f"Fetched {len(runs_page)} runs from API page.")
        found_run_before_day = False
        for run in runs_page:
            run_timestamp_str = run.get('created_at')
            if not run_timestamp_str: continue
            try:
                run_time_utc = datetime.fromisoformat(run_timestamp_str.replace('Z', '+00:00'))
                if run_time_utc.tzinfo is None: run_time_utc = run_time_utc.replace(tzinfo=timezone.utc)
            except ValueError: continue

            if day_start_utc <= run_time_utc < day_end_utc:
                all_runs_today.append(run)
            elif run_time_utc < day_start_utc:
                st.write(f"Found run {run.get('id')} ({run_timestamp_str}) before target date. Stopping pagination.")
                found_run_before_day = True
                break
        
        if found_run_before_day: break
        if len(runs_page) < limit_per_page: break
        total_count = response_data.get('extra', {}).get('pagination', {}).get('total_count', -1)
        current_count_checked = offset + len(runs_page)
        if total_count != -1 and current_count_checked >= total_count: break
        offset += len(runs_page)
    else:
         st.warning(f"Stopped fetching after checking {max_pages_to_check} pages. Results might be incomplete.")

    st.write(f"Total runs found for {target_date.isoformat()} (after client-side filtering): {len(all_runs_today)}")
    return all_runs_today

# --- Function to calculate rolling 7-day averages and flags ---
def calculate_rolling_averages_and_flags(df):
    """Calculate rolling 7-day averages and flag jobs exceeding average runtime.
    
    Args:
        df (pd.DataFrame): DataFrame with run data
        
    Returns:
        pd.DataFrame: DataFrame with added columns for averages and flags
    """
    # Helper function to calculate duration from timestamps only (as decided)
    debug_count = 0
    def calculate_duration_from_timestamps(started_at_str, finished_at_str=None):
        """Calculate duration in seconds using only timestamps for consistency and real-time accuracy"""
        nonlocal debug_count
        
        if not started_at_str or pd.isna(started_at_str):
            return None
            
        try:
            # Parse start time with proper timezone handling
            start_time = pd.to_datetime(started_at_str)
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            
            # Debug removed - issue identified and fixed
            
            if finished_at_str and pd.notna(finished_at_str):
                # Completed run (Success/Error/Cancelled): use actual finish time
                end_time = pd.to_datetime(finished_at_str)
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)
                duration = (end_time - start_time).total_seconds()
                debug_count += 1
                return duration
            else:
                # In-progress run (Queued/Starting/Running): use current time for real-time elapsed duration
                now = datetime.now(timezone.utc)
                duration = (now - start_time).total_seconds()
                debug_count += 1
                return duration
                
        except (ValueError, TypeError) as e:
            return None
    
    if df.empty:
        return df
    
    # Debug: Removed - issue identified and fixed
    
    # Get the date range for the past 7 days from the latest run
    latest_date = df['created_at'].max().date()
    seven_days_ago = latest_date - timedelta(days=7)
    
    # Fetch runs from the past 7 days for average calculation
    st.write(f"Fetching rolling 7-day data ({seven_days_ago} to {latest_date}) for average calculations...")
    
    rolling_runs = []
    for days_back in range(8):  # Get 8 days to ensure we have enough data
        fetch_date = latest_date - timedelta(days=days_back)
        if fetch_date < seven_days_ago:
            continue
        daily_runs = get_runs_for_day(fetch_date)
        if daily_runs:
            rolling_runs.extend(daily_runs)
    

    if not rolling_runs:
        st.warning("No historical data found for rolling average calculation.")
        # Add empty columns and calculate current durations from timestamps
        df['7d_avg_duration'] = None
        # Calculate run duration from timestamps for display
        df['run_duration_seconds'] = df.apply(
            lambda row: calculate_duration_from_timestamps(
                row.get('started_at') or row.get('created_at'), 
                row.get('finished_at')
            ), axis=1
        )
        df['exceeds_average'] = False
        return df
    
    # Create DataFrame for rolling calculations
    rolling_df = pd.DataFrame(rolling_runs)
    rolling_df['created_at'] = pd.to_datetime(rolling_df['created_at'])
    
    # Filter to completed runs only for average calculation 
    # Completed: 10 = Success, 20 = Error, 30 = Cancelled
    # In-progress: 1 = Queued, 2 = Starting, 3 = Running
    completed_rolling = rolling_df[rolling_df['status'].isin([10, 20, 30])].copy()
    
    # Calculate duration from timestamps for averaging
    completed_rolling['duration_seconds'] = completed_rolling.apply(
        lambda row: calculate_duration_from_timestamps(
            row.get('started_at') or row.get('created_at'), 
            row.get('finished_at')
        ), axis=1
    )
    
    # Remove rows where duration couldn't be calculated
    completed_rolling = completed_rolling[completed_rolling['duration_seconds'].notna()]
    
    # Calculate 7-day averages per job (in seconds)
    job_averages = completed_rolling.groupby('job_definition_id')['duration_seconds'].mean().to_dict()
    
    # Add 7-day average column
    df['7d_avg_duration'] = df['job_definition_id'].map(job_averages)
    
    # Calculate current/elapsed duration for each run
    current_durations = []
    exceeds_flags = []
    
    for _, row in df.iterrows():
        job_avg = row['7d_avg_duration']
        
        # Calculate actual duration from timestamps (completed jobs use finished_at, in-progress use current time)
        started_at = row.get('started_at') or row.get('created_at')
        finished_at = row.get('finished_at')
        current_duration = calculate_duration_from_timestamps(started_at, finished_at)
        
        current_durations.append(current_duration)
        
        # Check if it exceeds average (only if we have valid durations)
        if (pd.notna(job_avg) and current_duration is not None and 
            current_duration > job_avg):
            exceeds_flags.append(True)
        else:
            exceeds_flags.append(False)
    
    # Store the calculated duration for display (timestamp-based calculation)
    df['run_duration_seconds'] = current_durations
    df['exceeds_average'] = exceeds_flags
    
    return df

# --- Streamlit App UI ---
st.set_page_config(layout="wide", page_title="dbt Cloud Run Monitor")
st.title("dbt Cloud Job Run Monitor")
st.markdown(f"Account ID: `{ACCOUNT_ID}` | Base URL: `{BASE_URL}`")

# --- Date Selection --- 
today = date.today()
# Use a list/tuple for value to enable range selection
selected_date_range = st.date_input(
    "Select Date Range to View Runs", 
    value=(today, today), # Default to today
    max_value=today # Prevent selecting future dates
    )

# Ensure we have a start and end date (date_input returns tuple for range)
start_date = None
end_date = None
if isinstance(selected_date_range, (list, tuple)) and len(selected_date_range) == 2:
    start_date, end_date = selected_date_range
    if start_date > end_date:
        st.warning("Start date cannot be after end date. Using end date for both.")
        start_date = end_date # Or swap them
elif isinstance(selected_date_range, date): # Handle case where user might force single date
     start_date = selected_date_range
     end_date = selected_date_range
else:
    st.error("Invalid date range selected.")
    st.stop() # Stop execution if date range is invalid

# Use columns for layout
col1, col2 = st.columns([1, 3])

with col1:
    # Initialize session state for dataframe if it doesn't exist
    if 'runs_df' not in st.session_state:
        st.session_state['runs_df'] = pd.DataFrame()

    # Add cache clear button for recent runs
    if st.button("🔄 Clear Cache & Refresh", help="Clear cache to fetch the most recent runs"):
        st.cache_data.clear()
        st.session_state['runs_df'] = pd.DataFrame()
        st.rerun()

    if st.button("Fetch Runs", type="primary"):
        # Clear previous results before fetching new ones
        st.session_state['runs_df'] = pd.DataFrame()
        all_runs_in_range = [] # List to hold results from all days
        
        # Calculate date range
        if start_date and end_date:
             date_list = [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1)]
             spinner_text = f"Fetching data from {start_date.isoformat()} to {end_date.isoformat()}..."
        else: # Should not happen due to checks above, but fallback
             date_list = []
             spinner_text = "Fetching data..."

        with st.spinner(spinner_text):
            # 1. Fetch Runs for each day in the range
            total_runs_fetched = 0
            fetch_failed = False
            for current_date in date_list:
                st.write(f"--- Fetching for {current_date.isoformat()} ---")
                runs_for_current_day = get_runs_for_day(current_date) # Use existing cached function
                if runs_for_current_day is None:
                    # If get_runs_for_day returns None, it indicates an API error during fetch
                    st.error(f"Failed to fetch runs for {current_date.isoformat()}. Stopping.")
                    fetch_failed = True
                    break # Stop fetching if one day fails
                elif runs_for_current_day: # Check if list is not empty
                    all_runs_in_range.extend(runs_for_current_day)
                    total_runs_fetched += len(runs_for_current_day)
                    st.write(f"-> Found {len(runs_for_current_day)} runs.")
                else:
                     st.write(f"-> Found 0 runs.")

            if fetch_failed:
                 st.stop() # Stop if any day failed
            
            if not all_runs_in_range:
                st.warning("No runs found for the selected date range.")
                st.session_state['runs_df'] = pd.DataFrame() # Ensure it's an empty DF
                st.stop()

            st.write(f"--- Total runs found in range: {len(all_runs_in_range)} ---")
            runs_df = pd.DataFrame(all_runs_in_range)
            runs_df['created_at'] = pd.to_datetime(runs_df['created_at'])

            # 2. Fetch related definitions (Jobs, Projects, Environments) - Only needed once
            st.write("Fetching related definitions (Jobs, Projects, Environments)...")
            all_jobs = get_all_jobs()
            all_projects = get_all_projects()
            all_environments = get_all_environments()

            if all_jobs is None or all_projects is None or all_environments is None:
                st.error("Failed to fetch necessary definitions. Cannot enrich data.")
                st.session_state['runs_df'] = runs_df # Store unenriched data
                st.stop()

            # 3. Create Lookup Mappings
            job_map = {job['id']: {'name': job.get('name', f"Job {job['id']}"),
                                   'project_id': job.get('project_id'),
                                   'environment_id': job.get('environment_id')} 
                       for job in all_jobs}
            project_map = {proj['id']: proj.get('name', f"Project {proj['id']}") for proj in all_projects}
            environment_map = {env['id']: env.get('name', f"Env {env['id']}") for env in all_environments}
            # Complete status mapping for dbt Cloud jobs
            status_map = { 
                1: "Queued", 
                2: "Starting",
                3: "Running", 
                10: "Success", 
                20: "Error", 
                30: "Cancelled" 
            }

            # 4. Enrich DataFrame
            st.write("Enriching run data...") 
            if 'status' in runs_df.columns:
                runs_df['Status Name'] = runs_df['status'].map(lambda x: status_map.get(x, f"Unknown ({x})"))
            else:
                 st.warning("Column 'status' not found in run data. Cannot determine status names.")
                 runs_df['Status Name'] = 'Status N/A'
            runs_df['Job Name'] = runs_df['job_definition_id'].map(lambda x: job_map.get(x, {}).get('name', f"Unknown Job {x}"))
            # Use direct project_id from API response
            runs_df['Project ID'] = runs_df['project_id']
            runs_df['Environment ID'] = runs_df['environment_id']
            runs_df['Project Name'] = runs_df['Project ID'].map(lambda x: project_map.get(x, f"Unknown Project {x}"))
            runs_df['Environment Name'] = runs_df['Environment ID'].map(lambda x: environment_map.get(x, f"Unknown Env {x}"))

            # 5. Select and Store Final DataFrame
            cols_to_keep = [
                'id', 'Status Name', 'Job Name', 'Project Name', 'Project ID', 'Environment Name', 'Environment ID',
                'created_at', 'duration', 'job_definition_id', 'status', 'started_at', 'finished_at',
                'git_branch', 'git_sha'
            ]
            existing_cols = [col for col in cols_to_keep if col in runs_df.columns]
            final_df = runs_df[existing_cols].copy()
            
            # 6. Calculate Rolling 7-Day Averages and Flags
            st.write("Calculating rolling 7-day averages...")
            final_df = calculate_rolling_averages_and_flags(final_df)
            
            # Sort by creation time descending for consistent display
            final_df = final_df.sort_values(by='created_at', ascending=False)

            st.session_state['runs_df'] = final_df # Update session state
            st.success(f"Successfully fetched and enriched {len(final_df)} runs for the selected range.")
            st.rerun()

# --- Display Data and Filters (if data exists in session state) ---
if not st.session_state.runs_df.empty:
    df = st.session_state.runs_df.copy() 

    # Prepare filter options from the DataFrame
    available_statuses = sorted(df['Status Name'].unique())
    available_projects = sorted(df['Project Name'].unique())
    available_environments = sorted(df['Environment Name'].unique())
    available_jobs = sorted(df['Job Name'].unique())

    with col1: # Filters Sidebar
        st.metric("Total Runs Fetched (Range)", len(df)) # Show total fetched for the range
        st.write("**Filter Runs:**")
        selected_statuses = st.multiselect("Status", available_statuses, default=available_statuses)
        selected_projects = st.multiselect("Project", available_projects, default=available_projects)
        selected_environments = st.multiselect("Environment", available_environments, default=available_environments)
        selected_jobs = st.multiselect("Job Name", available_jobs, default=available_jobs)
        
        # Add filter for jobs exceeding average
        show_exceeding_only = st.checkbox("Show only jobs exceeding 7d average", value=False)

    # Apply Filters
    filter_conditions = (
        df['Status Name'].isin(selected_statuses) &
        df['Project Name'].isin(selected_projects) &
        df['Environment Name'].isin(selected_environments) &
        df['Job Name'].isin(selected_jobs)
    )
    
    # Add exceeding average filter if enabled
    if show_exceeding_only:
        filter_conditions = filter_conditions & df['exceeds_average']
    
    filtered_df = df[filter_conditions].copy() 

    with col2: # Main display area
        st.write(f"**Range Summary ({start_date.isoformat()} to {end_date.isoformat()})**") # Update title
        # Calculate Metrics from the potentially filtered DataFrame
        total_runs_display = len(filtered_df)
        successful_runs = len(filtered_df[filtered_df['Status Name'] == 'Success'])
        failed_runs = len(filtered_df[filtered_df['Status Name'] == 'Error'])
        cancelled_runs = len(filtered_df[filtered_df['Status Name'] == 'Cancelled'])
        exceeding_runs = len(filtered_df[filtered_df['exceeds_average'] == True])
        
        # Display Metrics in columns
        metric_cols = st.columns(5)
        with metric_cols[0]: st.metric("Total Runs (Filtered)", total_runs_display)
        with metric_cols[1]: st.metric("Successful", successful_runs, delta_color="off") 
        with metric_cols[2]: st.metric("Failed/Errored", failed_runs, delta_color="inverse" if failed_runs > 0 else "off")
        with metric_cols[3]: st.metric("Cancelled", cancelled_runs, delta_color="off")
        with metric_cols[4]: st.metric("Exceeding 7d Avg", exceeding_runs, delta_color="inverse" if exceeding_runs > 0 else "off")
        
        st.divider() 
        
        st.write(f"**Run Details ({len(filtered_df)} of {len(df)} runs shown after filtering)**")
        
        # Display duration columns in their original format, no formatting  
        display_df = filtered_df.copy()
        display_df['⚠️ Exceeds Avg'] = display_df['exceeds_average'].apply(lambda x: "🔴 Yes" if x else "✅ No")
        
        
        # Add URLs for clickable hyperlinks to dbt Cloud UI
        def create_run_url(row):
            run_id = row.get('id')
            project_id = row.get('Project ID')
            
            if pd.notna(run_id) and pd.notna(project_id):
                return f"{BASE_URL}/deploy/{ACCOUNT_ID}/projects/{project_id}/runs/{run_id}"
            else:
                return None
 
        def create_job_url(row):
            job_id = row.get('job_definition_id')
            project_id = row.get('Project ID')
            
            if pd.notna(job_id) and pd.notna(project_id):
                return f"{BASE_URL}/deploy/{ACCOUNT_ID}/projects/{project_id}/jobs/{job_id}"
            else:
                return None

        # Create separate ID and URL columns for LinkColumn configuration
        display_df['Run ID'] = display_df['id']
        display_df['Run URL'] = display_df.apply(create_run_url, axis=1)
        display_df['Job ID'] = display_df['job_definition_id'] 
        display_df['Job URL'] = display_df.apply(create_job_url, axis=1)

        # Define columns to display in the main table
        display_cols = ['Run ID', 'Job ID', 'Status Name', 'Job Name', 'Project Name', 'Environment Name', 
                       'created_at', 'run_duration_seconds', '7d_avg_duration', '⚠️ Exceeds Avg']
        display_cols_existing = [col for col in display_cols if col in display_df.columns]
 
        # For LinkColumn with display_text, we need the URL in the column value
        # and the ID values in separate columns for display_text reference
        display_df['Run ID'] = display_df.apply(
            lambda row: row['Run URL'] if pd.notna(row['Run URL']) else str(row['id']), 
            axis=1
        )
        display_df['Job ID'] = display_df.apply(
            lambda row: row['Job URL'] if pd.notna(row['Job URL']) else str(row['job_definition_id']), 
            axis=1
        )
        
        # Add ID columns for display_text to reference
        display_df['run_id_display'] = display_df['id'].astype(str)
        display_df['job_id_display'] = display_df['job_definition_id'].astype(str)

        # Configure columns with LinkColumn and display_text using regex substitution
        column_config = {
            'Run ID': st.column_config.LinkColumn(
                "🔗 Run ID",
                help="Click to view run in dbt Cloud",
                width="small",
                display_text=r"(\d+)$"  # Extract just the number at the end of URL
            ),
            'Job ID': st.column_config.LinkColumn(
                "🔗 Job ID", 
                help="Click to view job in dbt Cloud",
                width="small", 
                display_text=r"(\d+)$"  # Extract just the number at the end of URL
            ),
            'run_duration_seconds': st.column_config.NumberColumn(
                "📏 Duration (sec)",
                help="Run duration: completed jobs use actual finish time, in-progress jobs show elapsed time",
                format="%.1f"
            ),
            '7d_avg_duration': st.column_config.NumberColumn(
                "📊 7d Avg (sec)",
                help="7-day rolling average duration",
                format="%.1f"
            )
        }

        st.dataframe(
            display_df[display_cols_existing], 
            use_container_width=True, 
            height=600,
            column_config=column_config
        )

        st.caption("💡 Click on Run ID or Job ID links to open them in dbt Cloud")

        # Add summary for exceeding jobs
        if not filtered_df.empty and 'exceeds_average' in filtered_df.columns:
            exceeding_df = filtered_df[filtered_df['exceeds_average'] == True]
            if not exceeding_df.empty:
                st.write("**⚠️ Jobs Exceeding 7-Day Average**")
                exceeding_summary = exceeding_df.groupby('Job Name').agg({
                    'exceeds_average': 'count',
                    '7d_avg_duration': 'first'
                }).reset_index()
                exceeding_summary.columns = ['Job Name', 'Exceeding Runs', '7d_avg_duration']
                st.dataframe(exceeding_summary, use_container_width=True)

        st.write("**Run Status Summary (Filtered)**")
        if not filtered_df.empty:
            status_counts = filtered_df['Status Name'].value_counts().reset_index()
            status_counts.columns = ['Status', 'Count']
            st.dataframe(status_counts, use_container_width=True)
        else:
             st.write("(No runs match current filters)")