# Vexa Test Library

A comprehensive testing library for the Vexa client that provides advanced testing capabilities for multi-user scenarios.

## Features

- **Multi-User Management**: Create multiple users with individual API keys
- **Random Mapping**: Automatically distribute users across meetings
- **Bot Lifecycle Management**: Complete bot creation, monitoring, and cleanup
- **Background Monitoring**: Real-time monitoring with timestamped data
- **Pandas Integration**: Seamless integration with pandas DataFrames for analysis
- **Notebook-Friendly**: Designed for Jupyter notebook workflows

## Installation

The test library uses the `vexa_client` package. Install it first:

```bash
pip install vexa_client
```

## Quick Start

```python
import os
from new_tests import TestSuite

# Initialize test suite
test_suite = TestSuite(
    base_url="http://localhost:18056",
    admin_api_key=os.getenv('ADMIN_API_TOKEN'),
    poll_interval=2.0
)

# Create users and bots
users = test_suite.create_users(3)
mapping = test_suite.create_random_mapping([
    "https://teams.live.com/meet/1234567890123?p=TestPasscode",
    "https://meet.google.com/abc-defg-hij"
])
bots = test_suite.create_bots()

# Start bots and monitoring
test_suite.start_all_bots()
test_suite.start_monitoring()

# Monitor in real-time
df = test_suite.get_latest_dataframe()
print(df)

# Cleanup
test_suite.cleanup()
```

## Classes

### TestSuite

Main class for managing test scenarios.

#### Methods

- `create_users(num_users)`: Create multiple users with API keys
- `create_random_mapping(meeting_urls)`: Randomly distribute users to meetings
- `create_bots(bot_name_prefix)`: Create Bot instances based on mapping
- `start_all_bots(language, task)`: Start all bots simultaneously
- `stop_all_bots()`: Stop all running bots
- `start_monitoring()`: Start background monitoring
- `stop_monitoring()`: Stop background monitoring
- `snapshot()`: Take current state snapshot
- `parse_for_pandas(snapshot_data)`: Convert snapshot to pandas format
- `get_latest_dataframe()`: Get latest data as DataFrame
- `get_all_dataframe()`: Get all historical data as DataFrame
- `cleanup()`: Clean up all resources

### Bot

Individual bot instance for managing single bot operations.

#### Methods

- `create(bot_name, language, task)`: Create/request bot for meeting
- `get_transcript()`: Get current transcript
- `get_meeting_status()`: Get meeting status
- `stop()`: Stop the bot
- `update_config(language, task)`: Update bot configuration
- `get_stats()`: Get bot statistics

## Example Usage

See `demo_notebook.ipynb` for a complete example demonstrating:

1. User creation
2. Random user-meeting mapping
3. Bot lifecycle management
4. Real-time monitoring
5. Data analysis with pandas
6. Cleanup procedures

## Configuration

### Environment Variables

- `ADMIN_API_TOKEN`: Admin API key for user creation

### Test Parameters

- `base_url`: Vexa API base URL (default: "http://localhost:18056")
- `poll_interval`: Monitoring interval in seconds (default: 2.0)
- `num_users`: Number of users to create
- `meeting_urls`: List of meeting URLs for testing

## Data Structure

The library provides data in pandas-friendly format with columns:

- `timestamp`: Unix timestamp
- `datetime`: ISO datetime string
- `bot_id`: Unique bot identifier
- `meeting_url`: Full meeting URL
- `platform`: Meeting platform (teams, google_meet)
- `native_meeting_id`: Platform-specific meeting ID
- `created`: Bot creation status
- `meeting_status`: Current meeting status
- `start_time`: Meeting start time
- `end_time`: Meeting end time
- `first_transcript_time`: First transcript timestamp
- `last_transcript_time`: Last transcript timestamp
- `segments_count`: Number of transcript segments
- `has_transcript`: Boolean transcript availability
- `last_segment_time`: Last segment timestamp

## Advanced Features

### Real-time Monitoring Loop

```python
# Run this in a loop for continuous monitoring
for _ in range(10):
    df = test_suite.get_latest_dataframe()
    clear_output(wait=True)
    display(df[['bot_id', 'created', 'segments_count', 'has_transcript']])
    time.sleep(2)
```

### Data Analysis

```python
# Analyze transcript growth over time
all_df = test_suite.get_all_dataframe()
if not all_df.empty:
    all_df['datetime'] = pd.to_datetime(all_df['datetime'])
    
    # Plot segments over time
    import matplotlib.pyplot as plt
    for bot_id in all_df['bot_id'].unique():
        bot_data = all_df[all_df['bot_id'] == bot_id]
        plt.plot(bot_data['datetime'], bot_data['segments_count'], label=bot_id)
    
    plt.legend()
    plt.title('Transcript Segments Over Time')
    plt.show()
```

### Custom Bot Configuration

```python
# Create bots with specific configurations
for i, bot in enumerate(test_suite.bots):
    bot.create(
        bot_name=f"CustomBot_{i}",
        language='es',  # Spanish
        task='translate'  # Translation task
    )
```

## Error Handling

The library includes comprehensive error handling:

- Bot creation failures are logged and tracked
- Transcript retrieval errors are captured
- Monitoring continues even if individual bots fail
- Cleanup operations are safe to run multiple times

## Thread Safety

- Background monitoring runs in a separate thread
- All operations are thread-safe
- Monitoring can be started/stopped safely

## Requirements

- Python 3.7+
- vexa_client package
- pandas
- requests
- threading (built-in)

## License

This test library is part of the Vexa project and follows the same license terms.
