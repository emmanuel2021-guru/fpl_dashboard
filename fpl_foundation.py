import requests
import pandas as pd

def fetch_core_fpl_data():
    """Fetches the overarching game state from the bootstrap-static endpoint."""
    url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
    print("Fetching data from FPL API...")
    response = requests.get(url)
    
    if response.status_code == 200:
        print("Data fetched successfully!")
        return response.json()
    else:
        print(f"Failed to fetch data. Status code: {response.status_code}")
        return None

def process_and_save(data):
    """Parses the JSON data into Pandas DataFrames and saves them as CSVs."""
    
    # 1. Process Players (Elements)
    players_df = pd.DataFrame(data['elements'])
    
    # The elements payload is massive. Let's filter it down to the most crucial columns for now.
    # 'now_cost' is the price (divide by 10 for true price), 'ep_next' is expected points next gameweek.
    player_columns = [
        'id', 'web_name', 'team', 'element_type', 'now_cost', 
        'total_points', 'form', 'points_per_game', 'ep_next', 
        'expected_goal_involvements', 'minutes'
    ]
    players_df = players_df[player_columns]
    
    # Convert price to proper format (e.g., 77 becomes 7.7)
    players_df['now_cost'] = players_df['now_cost'] / 10
    
    players_df.to_csv('fpl_players.csv', index=False)
    print("✅ Saved player data to 'fpl_players.csv'")

    # 2. Process Teams
    teams_df = pd.DataFrame(data['teams'])
    team_columns = ['id', 'name', 'short_name', 'strength', 'strength_overall_home', 'strength_overall_away']
    teams_df = teams_df[team_columns]
    teams_df.to_csv('fpl_teams.csv', index=False)
    print("✅ Saved team data to 'fpl_teams.csv'")

    # 3. Process Gameweeks (Events)
    events_df = pd.DataFrame(data['events'])
    event_columns = ['id', 'name', 'deadline_time', 'finished', 'is_current', 'is_next']
    events_df = events_df[event_columns]
    events_df.to_csv('fpl_gameweeks.csv', index=False)
    print("✅ Saved gameweek data to 'fpl_gameweeks.csv'")

if __name__ == '__main__':
    fpl_data = fetch_core_fpl_data()
    if fpl_data:
        process_and_save(fpl_data)
        print("Phase 1 Complete! You now have your core data pipeline.")