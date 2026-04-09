import pandas as pd
import requests

def fetch_future_fixtures():
    """Fetches all upcoming matches from the FPL API."""
    print("Fetching upcoming fixtures...")
    # The ?future=1 parameter ensures we only get matches that haven't happened yet
    url = 'https://fantasy.premierleague.com/api/fixtures/?future=1'
    response = requests.get(url)
    return response.json()

def calculate_team_fdr(fixtures_data, next_n_games=3):
    """Calculates the average fixture difficulty for the next N games for each team."""
    fixtures_df = pd.DataFrame(fixtures_data)
    
    team_fdr = {}
    
    # Iterate through teams 1 to 20
    for team_id in range(1, 21):
        # Find matches where the team is playing at home or away
        home_matches = fixtures_df[fixtures_df['team_h'] == team_id][['event', 'team_h_difficulty']].rename(columns={'team_h_difficulty': 'difficulty'})
        away_matches = fixtures_df[fixtures_df['team_a'] == team_id][['event', 'team_a_difficulty']].rename(columns={'team_a_difficulty': 'difficulty'})
        
        # Combine home and away, sort by gameweek (event), and take the next N games
        team_matches = pd.concat([home_matches, away_matches]).sort_values('event').head(next_n_games)
        
        # Calculate the average difficulty (lower is better)
        avg_difficulty = team_matches['difficulty'].mean()
        team_fdr[team_id] = round(avg_difficulty, 2)
        
    return team_fdr

def generate_transfer_targets():
    """Combines player stats with fixture difficulty to find the best buys."""
    print("Loading data from Phase 1...")
    players_df = pd.read_csv('fpl_players.csv')
    teams_df = pd.read_csv('fpl_teams.csv')
    
    fixtures_data = fetch_future_fixtures()
    team_fdr_dict = calculate_team_fdr(fixtures_data, next_n_games=3)
    
    # Map the calculated FDR to the teams dataframe, then merge with players
    teams_df['next_3_fdr'] = teams_df['id'].map(team_fdr_dict)
    
    # Merge player data with their team's upcoming FDR
    players_df = players_df.merge(teams_df[['id', 'name', 'next_3_fdr']], left_on='team', right_on='id', suffixes=('', '_team'))
    
    # CLEANING & FILTERING
    # Let's only look at players who actually play (e.g., more than 300 minutes this season)
    active_players = players_df[players_df['minutes'] > 300].copy()
    
    # Convert 'ep_next' (Expected Points Next GW) and 'expected_goal_involvements' to floats
    active_players['ep_next'] = active_players['ep_next'].astype(float)
    active_players['expected_goal_involvements'] = active_players['expected_goal_involvements'].astype(float)
    
    # THE ALGORITHM: Calculate a Custom "Buy Rating"
    # High Form + High Expected Goal Involvements + Low FDR (Easy Fixtures) = High Buy Rating
    # Note: We do (5 - next_3_fdr) to invert difficulty, so a 2.0 difficulty yields a 3.0 boost!
    active_players['buy_rating'] = (
        (active_players['form'] * 0.4) + 
        (active_players['expected_goal_involvements'] * 0.4) + 
        ((5 - active_players['next_3_fdr']) * 0.2)
    )
    
    # Sort the players by our new Buy Rating in descending order
    top_targets = active_players.sort_values('buy_rating', ascending=False)
    
    # Select the most useful columns to output
    final_columns = [
        'web_name', 'name', 'element_type', 'now_cost', 'form', 
        'expected_goal_involvements', 'next_3_fdr', 'ep_next', 'buy_rating'
    ]
    
    final_df = top_targets[final_columns].round(2)
    
    # Save the master target list
    final_df.to_csv('fpl_transfer_targets.csv', index=False)
    print("✅ Algorithm complete! Saved top targets to 'fpl_transfer_targets.csv'")
    
    # Print the top 5 targets to the console
    print("\n--- TOP 5 TRANSFER TARGETS RIGHT NOW ---")
    print(final_df.head(5).to_string(index=False))

if __name__ == '__main__':
    generate_transfer_targets()