import streamlit as st
import pandas as pd
import requests
import pulp
import math

from fpl_foundation import fetch_core_fpl_data, process_and_save
from fpl_phase2 import generate_transfer_targets

# --- APP CONFIGURATION ---
st.set_page_config(page_title="FPL Auto-Analyzer", layout="wide", page_icon="⚽")
st.title("🏆 Ultimate FPL Auto-Analyzer")

fpl_data = fetch_core_fpl_data()
if fpl_data:
    process_and_save(fpl_data)
    print("Phase 1 Complete! You now have your core data pipeline.")

generate_transfer_targets()

# --- 1. CACHED DATA LOADING ---
@st.cache_data
def load_csv_data():
    try:
        players_df = pd.read_csv('fpl_players.csv')
        targets_df = pd.read_csv('fpl_transfer_targets.csv')
        gw_df = pd.read_csv('fpl_gameweeks.csv')
        return players_df, targets_df, gw_df
    except FileNotFoundError:
        st.error("❌ Missing CSV files! Make sure Phase 1 and Phase 2 scripts have been run.")
        return None, None, None

# --- 2. CORE LOGIC & FINANCIAL TRACER ---
def get_current_gameweek(gw_df):
    try:
        current_gw = gw_df[(gw_df['is_current'] == True) | (gw_df['is_current'].astype(str).str.lower() == 'true')]
        if not current_gw.empty:
            return int(current_gw.iloc[0]['id'])
            
        finished_gws = gw_df[(gw_df['finished'] == True) | (gw_df['finished'].astype(str).str.lower() == 'true')]
        if not finished_gws.empty:
            return int(finished_gws['id'].max())
        return 1
    except Exception as e:
        return 1

def fetch_manager_data(manager_id, gw):
    headers = {'User-Agent': 'Mozilla/5.0'}
    valid_picks = None
    current_check = gw
    
    with st.spinner(f"Locating team data for Manager {manager_id}..."):
        while current_check > 0:
            url = f'https://fantasy.premierleague.com/api/entry/{manager_id}/event/{current_check}/picks/'
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                
                # THE FREE HIT FIX: Skip the fake squad and grab the permanent one
                if data.get('active_chip') == 'freehit' and current_check == gw:
                    st.toast("🔥 Free Hit detected! Reverting to your actual permanent squad...", icon="⚠️")
                    current_check -= 1
                    continue
                    
                valid_picks = data
                break
            else:
                current_check -= 1
                
    if not valid_picks:
        return None

    bank_balance = 0.0
    if 'entry_history' in valid_picks and valid_picks['entry_history'] is not None:
        bank_balance = valid_picks['entry_history'].get('bank', 0) / 10.0
        
    team_df = pd.DataFrame(valid_picks['picks'])
    return {"team": team_df, "bank": bank_balance}

def get_player_financials(manager_id, current_squad_ids):
    """Traces transfer history to find exact purchase price, profit, and real selling price."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 1. Get current prices and GW1 starting prices
    bootstrap_res = requests.get('https://fantasy.premierleague.com/api/bootstrap-static/', headers=headers)
    elements = bootstrap_res.json()['elements']
    
    financials = {}
    for el in elements:
        if el['id'] in current_squad_ids:
            now_cost = el['now_cost'] / 10.0
            cost_change = el['cost_change_start'] / 10.0
            gw1_cost = now_cost - cost_change
            financials[el['id']] = {'now_cost': now_cost, 'purchase_price': gw1_cost}
            
    # 2. Trace all past transfers to overwrite GW1 price with actual purchase price
    transfers_res = requests.get(f'https://fantasy.premierleague.com/api/entry/{manager_id}/transfers/', headers=headers)
    if transfers_res.status_code == 200:
        transfers = transfers_res.json()
        transfers.sort(key=lambda x: x['time']) # Sort oldest to newest
        
        for t in transfers:
            p_in = t['element_in']
            if p_in in current_squad_ids:
                financials[p_in]['purchase_price'] = t['element_in_cost'] / 10.0
                
    # 3. Calculate Profit and Apply the 50% Tax Rule for Selling Price
    for pid, data in financials.items():
        now = data['now_cost']
        bought = data['purchase_price']
        profit = now - bought
        
        if profit > 0:
            # FPL rounds down profit to nearest 0.1m
            sell_price = bought + (math.floor(profit * 10 / 2) / 10.0)
        else:
            sell_price = now
            
        data['selling_price'] = sell_price
        data['profit'] = profit
        
    return financials

# def get_available_chips(manager_id):
#     """Checks the FPL API for chips the manager has already used."""
#     headers = {'User-Agent': 'Mozilla/5.0'}
#     res = requests.get(f'https://fantasy.premierleague.com/api/entry/{manager_id}/history/', headers=headers)
#     if res.status_code != 200: 
#         return []
        
#     history = res.json()
#     used_chips = [chip['name'] for chip in history.get('chips', [])]
    
#     available = []
#     if 'bboost' not in used_chips: available.append('Bench Boost')
#     if '3xc' not in used_chips: available.append('Triple Captain')
#     if 'freehit' not in used_chips: available.append('Free Hit')
    
#     # FPL managers get 2 wildcards a season
#     wc_used = used_chips.count('wildcard')
#     if wc_used == 0: available.append('Wildcard (x2)')
#     elif wc_used == 1: available.append('Wildcard (x1)')
    
#     return available

# def get_fixture_density(gw_start, lookahead=4):
#     """Scans the FPL schedule to find Blank and Double Gameweeks."""
#     headers = {'User-Agent': 'Mozilla/5.0'}
#     fix_res = requests.get('https://fantasy.premierleague.com/api/fixtures/', headers=headers)
#     teams_res = requests.get('https://fantasy.premierleague.com/api/bootstrap-static/', headers=headers)
    
#     if fix_res.status_code != 200 or teams_res.status_code != 200: 
#         return []
        
#     fixtures = fix_res.json()
#     teams = {t['id']: t['short_name'] for t in teams_res.json()['teams']}
    
#     gw_end = gw_start + lookahead - 1
#     relevant_fixtures = [f for f in fixtures if f['event'] and gw_start <= f['event'] <= gw_end]
    
#     density = {gw: {team_id: 0 for team_id in teams.keys()} for gw in range(gw_start, gw_end + 1)}
    
#     for f in relevant_fixtures:
#         if f['team_h'] in density[f['event']]: density[f['event']][f['team_h']] += 1
#         if f['team_a'] in density[f['event']]: density[f['event']][f['team_a']] += 1
        
#     report = []
#     for gw in range(gw_start, gw_end + 1):
#         bgw_teams = [teams[t] for t, count in density[gw].items() if count == 0]
#         dgw_teams = [teams[t] for t, count in density[gw].items() if count > 1]
#         report.append({"GW": gw, "Blanks": bgw_teams, "Doubles": dgw_teams})
        
#     return report

# def suggest_chip_strategy(density_report, available_chips):
#     """Generates chip advice based on upcoming fixture congestion."""
#     suggestions = []
#     for gw_data in density_report:
#         gw = gw_data['GW']
#         blanks = gw_data['Blanks']
#         doubles = gw_data['Doubles']
        
#         if len(blanks) >= 4 and 'Free Hit' in available_chips:
#             suggestions.append(f"⚠️ **GW{gw} Alert:** {len(blanks)} teams are blanking ({', '.join(blanks[:4])}...). Highly consider using your **Free Hit** here.")
#         elif len(blanks) > 0:
#             suggestions.append(f"ℹ️ **GW{gw} Minor Blank:** Watch out, {', '.join(blanks)} do not play.")
        
#         if len(doubles) >= 3:
#             if 'Bench Boost' in available_chips:
#                 suggestions.append(f"🔥 **GW{gw} Massive Double:** {len(doubles)} teams play twice! Perfect time for a **Bench Boost**.")
#             if 'Triple Captain' in available_chips:
#                 suggestions.append(f"👑 **GW{gw} Double Gameweek Alert:** Consider using **Triple Captain** on a premium player from {', '.join(doubles[:3])}.")
#         elif len(doubles) > 0:
#             if 'Triple Captain' in available_chips:
#                 suggestions.append(f"🎯 **GW{gw} Double:** {', '.join(doubles)} play twice. Could be a cheeky **Triple Captain** opportunity.")
                
#     if not suggestions:
#         suggestions.append("🧘 **Patience:** Keep your chips for now. No obvious major DGW/BGW chip strategies in the immediate horizon.")
        
#     return suggestions

def get_available_chips(manager_id):
    """Checks the FPL API for chips the manager has already used."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get(f'https://fantasy.premierleague.com/api/entry/{manager_id}/history/', headers=headers)
    if res.status_code != 200: 
        return []
        
    history = res.json()
    # FIX: Force lowercase to ensure 'bboost' is always caught correctly regardless of API changes
    used_chips = [chip['name'].lower() for chip in history.get('chips', [])]
    
    available = []
    if 'bboost' not in used_chips: available.append('Bench Boost')
    if '3xc' not in used_chips: available.append('Triple Captain')
    if 'freehit' not in used_chips: available.append('Free Hit')
    
    # FPL managers get 2 wildcards a season
    wc_used = used_chips.count('wildcard')
    if wc_used == 0: available.append('Wildcard (x2)')
    elif wc_used == 1: available.append('Wildcard (x1)')
    
    return available

def get_fixture_density(gw_start, end_gw=38):
    """Scans the FPL schedule from the current GW to the end of the season."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    fix_res = requests.get('https://fantasy.premierleague.com/api/fixtures/', headers=headers)
    teams_res = requests.get('https://fantasy.premierleague.com/api/bootstrap-static/', headers=headers)
    
    if fix_res.status_code != 200 or teams_res.status_code != 200: 
        return []
        
    fixtures = fix_res.json()
    teams = {t['id']: t['short_name'] for t in teams_res.json()['teams']}
    
    relevant_fixtures = [f for f in fixtures if f['event'] and gw_start <= f['event'] <= end_gw]
    
    density = {gw: {team_id: 0 for team_id in teams.keys()} for gw in range(gw_start, end_gw + 1)}
    
    for f in relevant_fixtures:
        if f['team_h'] in density[f['event']]: density[f['event']][f['team_h']] += 1
        if f['team_a'] in density[f['event']]: density[f['event']][f['team_a']] += 1
        
    report = []
    for gw in range(gw_start, end_gw + 1):
        bgw_teams = [teams[t] for t, count in density[gw].items() if count == 0]
        dgw_teams = [teams[t] for t, count in density[gw].items() if count > 1]
        report.append({"GW": gw, "Blanks": bgw_teams, "Doubles": dgw_teams})
        
    return report

def suggest_chip_strategy(density_report, available_chips):
    """Generates a comprehensive, mapped roadmap for the remaining chips."""
    suggestions = []
    
    # Find all notable gameweeks
    bgws = [gw for gw in density_report if len(gw['Blanks']) > 0]
    dgws = [gw for gw in density_report if len(gw['Doubles']) > 0]
    
    # Sort by severity (biggest blanks/doubles first)
    bgws.sort(key=lambda x: len(x['Blanks']), reverse=True)
    dgws.sort(key=lambda x: len(x['Doubles']), reverse=True)
    
    planned_chips = {} # Track which GW we assigned a chip to avoid double-booking
    
    # 1. Map Free Hit to the biggest Blank Gameweek
    if 'Free Hit' in available_chips and bgws:
        biggest_bgw = bgws[0]
        if len(biggest_bgw['Blanks']) >= 4:
            planned_chips[biggest_bgw['GW']] = "Free Hit"
            suggestions.append(f"🔴 **Free Hit Strategy:** Play this in **GW{biggest_bgw['GW']}**. With {len(biggest_bgw['Blanks'])} teams blanking ({', '.join(biggest_bgw['Blanks'][:4])}...), this chip saves you from taking massive point hits.")

    # 2. Map Bench Boost to the biggest Double Gameweek
    if 'Bench Boost' in available_chips and dgws:
        for dgw in dgws:
            if dgw['GW'] not in planned_chips:
                planned_chips[dgw['GW']] = "Bench Boost"
                suggestions.append(f"🔥 **Bench Boost Strategy:** Target **GW{dgw['GW']}** where {len(dgw['Doubles'])} teams play twice. This maximizes the 15-man squad potential.")
                
                # 3. Map Wildcard to set up the Bench Boost (if available)
                wc_count = 2 if 'Wildcard (x2)' in available_chips else (1 if 'Wildcard (x1)' in available_chips else 0)
                if wc_count > 0:
                    target_wc_gw = dgw['GW'] - 1
                    # Ensure we don't suggest wildcarding in the past
                    if target_wc_gw >= density_report[0]['GW'] and target_wc_gw not in planned_chips:
                        planned_chips[target_wc_gw] = "Wildcard"
                        suggestions.append(f"🃏 **Wildcard Strategy:** Because you should Bench Boost in GW{dgw['GW']}, trigger your Wildcard in **GW{target_wc_gw}** to fill your squad and bench entirely with DGW players.")
                break 

    # 4. Map Triple Captain
    if 'Triple Captain' in available_chips and dgws:
        for dgw in dgws:
            if dgw['GW'] not in planned_chips:
                planned_chips[dgw['GW']] = "Triple Captain"
                suggestions.append(f"👑 **Triple Captain Strategy:** Attack **GW{dgw['GW']}**. Premium players from {', '.join(dgw['Doubles'][:3])} have two fixtures to secure a massive haul.")
                break
                
    # 5. Fallback for remaining chips with no obvious fixture targets
    for chip in available_chips:
        base_chip = chip.split(' (')[0]
        if base_chip not in [val for val in planned_chips.values()]:
            if base_chip == 'Wildcard':
                suggestions.append(f"🃏 **Wildcard:** No major Double Gameweeks immediately follow to prepare for. Use this strictly when your squad has 3+ long-term injuries or deep structural issues.")
            elif base_chip == 'Free Hit':
                suggestions.append(f"🔴 **Free Hit:** No massive Blank Gameweeks remaining. Save this for unforeseen postponements, or attack a standard week where top teams have highly favorable matchups.")
            elif base_chip == 'Bench Boost':
                 suggestions.append(f"🔥 **Bench Boost:** No massive Doubles left. Play this in a standard week where your 4 bench players all have excellent home fixtures against weak opposition.")
            elif base_chip == 'Triple Captain':
                suggestions.append(f"👑 **Triple Captain:** No massive Doubles left. Hold this until a premium player (like Haaland or Salah) plays a relegation-threatened team at home.")

    if not suggestions and not available_chips:
        suggestions.append("🧘 **No Chips Left:** Your focus now is pure long-term planning. Roll your free transfers where possible to give yourself flexibility!")
        
    return suggestions

def optimize_starting_lineup(team_df):
    prob = pulp.LpProblem("Best_XI", pulp.LpMaximize)
    player_vars = pulp.LpVariable.dicts("p", team_df.index, cat='Binary')
    
    team_df['ep_next'] = team_df['ep_next'].astype(float).fillna(0.0)
    team_df['now_cost'] = team_df['now_cost'].astype(float).fillna(0.0)
    if 'next_3_fdr' not in team_df.columns: team_df['next_3_fdr'] = 3.0
    else: team_df['next_3_fdr'] = team_df['next_3_fdr'].astype(float).fillna(3.0)
    
    team_df['starter_score'] = team_df['ep_next'] + ((5.0 - team_df['next_3_fdr']) * 0.5) + (team_df['now_cost'] * 0.2)
    team_df.loc[team_df['ep_next'] <= 0, 'starter_score'] = 0.0
    
    prob += pulp.lpSum([team_df.loc[i, 'starter_score'] * player_vars[i] for i in team_df.index])
    elem_col = 'element_type_x' if 'element_type_x' in team_df.columns else 'element_type'
    
    prob += pulp.lpSum([player_vars[i] for i in team_df.index]) == 11
    prob += pulp.lpSum([player_vars[i] for i in team_df.index if team_df.loc[i, elem_col] == 1]) == 1
    prob += pulp.lpSum([player_vars[i] for i in team_df.index if team_df.loc[i, elem_col] == 2]) >= 3
    prob += pulp.lpSum([player_vars[i] for i in team_df.index if team_df.loc[i, elem_col] == 2]) <= 5
    prob += pulp.lpSum([player_vars[i] for i in team_df.index if team_df.loc[i, elem_col] == 3]) >= 2
    prob += pulp.lpSum([player_vars[i] for i in team_df.index if team_df.loc[i, elem_col] == 3]) <= 5
    prob += pulp.lpSum([player_vars[i] for i in team_df.index if team_df.loc[i, elem_col] == 4]) >= 1
    prob += pulp.lpSum([player_vars[i] for i in team_df.index if team_df.loc[i, elem_col] == 4]) <= 3

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    
    team_df['is_starter'] = [1 if player_vars[i].varValue == 1 else 0 for i in team_df.index]
    starters = team_df[team_df['is_starter'] == 1].copy().sort_values('starter_score', ascending=False).reset_index(drop=True)
    starters['Role'] = 'Starter'
    if not starters.empty:
        starters.loc[0, 'Role'] = '👑 Captain (C)'
        if len(starters) > 1: starters.loc[1, 'Role'] = '🥈 Vice (VC)'
            
    bench = team_df[team_df['is_starter'] == 0].copy()
    bench_gk = bench[bench[elem_col] == 1]
    bench_outfield = bench[bench[elem_col] != 1].sort_values('starter_score', ascending=False)
    ordered_bench = pd.concat([bench_gk, bench_outfield]).reset_index(drop=True)
    if not ordered_bench.empty: ordered_bench['Role'] = ['Sub GK'] + [f'Sub {i+1}' for i in range(len(bench_outfield))]
    
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    starters['Position'] = starters[elem_col].map(pos_map)
    ordered_bench['Position'] = ordered_bench[elem_col].map(pos_map)
    
    return starters, ordered_bench

def generate_wildcard(targets_df, budget):
    prob = pulp.LpProblem("FPL_Wildcard", pulp.LpMaximize)
    player_vars = pulp.LpVariable.dicts("player", targets_df.index, cat='Binary')
    prob += pulp.lpSum([targets_df.loc[i, 'buy_rating'] * player_vars[i] for i in targets_df.index])
    prob += pulp.lpSum([targets_df.loc[i, 'now_cost'] * player_vars[i] for i in targets_df.index]) <= budget
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index]) == 15
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 1]) == 2
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 2]) == 5
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 3]) == 5
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 4]) == 3
    teams = targets_df['name'].unique()
    for team in teams: prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'name'] == team]) <= 3
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    return targets_df.loc[[i for i in targets_df.index if player_vars[i].varValue == 1]].copy().sort_values(['element_type', 'buy_rating'], ascending=[True, False])

def generate_free_hit(targets_df, budget):
    prob = pulp.LpProblem("FPL_Free_Hit", pulp.LpMaximize)
    player_vars = pulp.LpVariable.dicts("player", targets_df.index, cat='Binary')
    targets_df['ep_next'] = targets_df['ep_next'].astype(float).fillna(0.0)
    prob += pulp.lpSum([targets_df.loc[i, 'ep_next'] * player_vars[i] for i in targets_df.index])
    prob += pulp.lpSum([targets_df.loc[i, 'now_cost'] * player_vars[i] for i in targets_df.index]) <= budget
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index]) == 15
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 1]) == 2
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 2]) == 5
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 3]) == 5
    prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'element_type'] == 4]) == 3
    teams = targets_df['name'].unique()
    for team in teams: prob += pulp.lpSum([player_vars[i] for i in targets_df.index if targets_df.loc[i, 'name'] == team]) <= 3
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    return targets_df.loc[[i for i in targets_df.index if player_vars[i].varValue == 1]].copy().sort_values(['element_type', 'ep_next'], ascending=[True, False])

# # --- 3. UI LAYOUT & INTERFACE ---
# players_df, targets_df, gw_df = load_csv_data()

# if players_df is not None:
#     st.sidebar.header("🛠️ Manager Settings")
#     manager_id_input = st.sidebar.text_input("Enter FPL Manager ID", "9478527")
#     free_transfers = st.sidebar.number_input("Free Transfers Available", min_value=1, max_value=5, value=1)
#     analyze_button = st.sidebar.button("Analyze My Team")

#     st.sidebar.header("🧠 AI Settings")
#     api_key = st.sidebar.text_input("Gemini API Key", type="password", help="Get a free key at aistudio.google.com")
    
#     tab1, tab2, tab3, tab4 = st.tabs(["📊 My Team Analysis", "🎯 Master Target List", "🃏 Smart Wildcard", "🔥 1-Week Free Hit"])
    
#     if "my_team" not in st.session_state:
#         st.session_state.my_team = None
#         st.session_state.bank = 0.0
#         st.session_state.sale_value = 100.0
#         st.session_state.total_value = 100.0

#     if analyze_button and manager_id_input.isdigit():
#         manager_id = int(manager_id_input)
#         gw = get_current_gameweek(gw_df)
#         manager_data = fetch_manager_data(manager_id, gw)
        
#         if manager_data:
#             my_team_raw = manager_data["team"]
#             st.session_state.bank = manager_data["bank"]
            
#             # --- THE FINANCIAL MERGE ---
#             squad_ids = my_team_raw['element'].tolist()
#             financials = get_player_financials(manager_id, squad_ids)
            
#             my_team_raw['purchase_price'] = my_team_raw['element'].map(lambda x: financials[x]['purchase_price'])
#             my_team_raw['selling_price'] = my_team_raw['element'].map(lambda x: financials[x]['selling_price'])
#             my_team_raw['profit'] = my_team_raw['element'].map(lambda x: financials[x]['profit'])
            
#             my_team_temp = my_team_raw.merge(players_df[['id', 'web_name', 'now_cost', 'element_type', 'ep_next']], left_on='element', right_on='id')
#             my_team_temp = my_team_temp.merge(targets_df[['web_name', 'buy_rating', 'next_3_fdr']], on='web_name', how='left')
#             my_team_temp['buy_rating'] = my_team_temp['buy_rating'].fillna(0.0)
            
#             st.session_state.total_value = round(my_team_temp['now_cost'].sum() + st.session_state.bank, 1)
#             st.session_state.sale_value = round(my_team_temp['selling_price'].sum() + st.session_state.bank, 1)
#             st.session_state.my_team = my_team_temp

#     my_team = st.session_state.my_team
#     bank = st.session_state.bank
#     sale_value = st.session_state.sale_value
#     total_value = st.session_state.total_value

#     # --- TAB 1: TEAM ANALYSIS ---
#     with tab1:
#         if my_team is not None:
#             st.success("✅ Data & Financials fetched successfully!")
#             c1, c2, c3 = st.columns(3)
#             c1.metric(label="💰 Money in the Bank", value=f"£{bank}m")
#             c2.metric(label="📈 Squad Value (Raw)", value=f"£{total_value}m")
#             c3.metric(label="📉 Real Sale Value", value=f"£{sale_value}m", help="This is your actual spending power after the 50% profit tax.")
#             st.divider()
            
#             col1, col2 = st.columns([1.5, 1])
            
#             with col1:
#                 st.subheader("📋 Optimal Starting XI & Bench")
#                 starters, bench = optimize_starting_lineup(my_team)
                
#                 st.markdown("**Starting 11 (Sorted by Match Rating + Captaincy)**")
#                 st.dataframe(starters[['Role', 'Position', 'web_name', 'ep_next', 'next_3_fdr', 'starter_score']], use_container_width=True, hide_index=True)
                
#                 st.markdown("**Auto-Subs Bench (Optimal Order)**")
#                 st.dataframe(bench[['Role', 'Position', 'web_name', 'ep_next', 'starter_score']], use_container_width=True, hide_index=True)
                
#             with col2:
#                 st.subheader("🔄 Transfer Optimizer")
                
#                 blanking_starters = starters[starters['ep_next'] <= 0]
#                 blanking_bench = bench[bench['ep_next'] <= 0]
#                 mids_and_fwds = my_team[(my_team['element_type_x'].isin([3, 4])) & (my_team['ep_next'] > 0)].sort_values('buy_rating', ascending=True)
#                 potential_sells = pd.concat([blanking_starters, blanking_bench, mids_and_fwds])
                
#                 current_bank = bank
#                 hyp_squad = my_team.copy()
#                 owned_names = hyp_squad['web_name'].tolist()
#                 transfers_made = 0
                
#                 for index, weakest_link in potential_sells.iterrows():
#                     if transfers_made >= free_transfers: break
                        
#                     # TRUE SELLING PRICE FIX
#                     sell_price = weakest_link['selling_price']
#                     available_funds = round(current_bank + sell_price, 1)
                    
#                     is_emergency = weakest_link['ep_next'] <= 0
#                     sell_reason = "Emergency (Blank/Injured)" if is_emergency else "Lowest Squad Rating"
                    
#                     affordable_targets = targets_df[
#                         (~targets_df['web_name'].isin(owned_names)) & 
#                         (targets_df['element_type'] == weakest_link['element_type_x']) &
#                         (targets_df['now_cost'] <= available_funds) &
#                         (targets_df['ep_next'].astype(float) > 0.5) 
#                     ].sort_values('buy_rating', ascending=False)
                    
#                     if not affordable_targets.empty:
#                         top_replacement = affordable_targets.iloc[0]
#                         point_swing = round(top_replacement['buy_rating'] - weakest_link['buy_rating'], 2)
                        
#                         if is_emergency or point_swing > 0:
#                             st.error(f"📉 **SELL:** {weakest_link['web_name']} (Sell Price: £{sell_price:.1f}m | Profit: £{weakest_link['profit']:.1f}m)")
#                             st.caption(f"Reason: {sell_reason}")
#                             st.success(f"📈 **BUY:** {top_replacement['web_name']} (£{top_replacement['now_cost']:.1f}m) - Rating: {round(top_replacement['buy_rating'], 2)}")
                            
#                             if is_emergency: st.metric(label="Expected Rating Swing", value="FIXED SQUAD")
#                             else: st.metric(label="Expected Rating Swing", value=f"+{point_swing}")
#                             st.divider()
                            
#                             current_bank = available_funds - top_replacement['now_cost']
#                             owned_names.append(top_replacement['web_name'])
                            
#                             hyp_squad = hyp_squad[hyp_squad['web_name'] != weakest_link['web_name']]
#                             new_player = top_replacement.to_frame().T
#                             new_player['element_type_x'] = new_player['element_type']
#                             hyp_squad = pd.concat([hyp_squad, new_player], ignore_index=True)
                            
#                             transfers_made += 1
                
#                 if transfers_made > 0:
#                     with st.expander(f"🔮 View Squad After ALL {transfers_made} Transfer(s)", expanded=False):
#                         hyp_starters, hyp_bench = optimize_starting_lineup(hyp_squad)
#                         st.markdown("**New Starting 11 & Captaincy**")
#                         st.dataframe(hyp_starters[['Role', 'Position', 'web_name', 'starter_score']], use_container_width=True, hide_index=True)
#                 else:
#                     st.info("Hold your transfers! Your current squad mathematically outperforms affordable alternatives.")
            
#             # --- NEW SECTION: FINANCIAL TRACKER ---
#             st.divider()
#             st.subheader("💵 Squad Financial Tracker")
#             st.write("Track exactly how much profit your players have made, and how much FPL will let you sell them for.")
            
#             display_fin = my_team[['web_name', 'purchase_price', 'now_cost', 'selling_price', 'profit']].copy()
#             # Rename columns for the UI
#             display_fin.columns = ['Player', 'Bought For (£)', 'Current Value (£)', 'Actual Sell Price (£)', 'Profit (£)']
#             display_fin = display_fin.sort_values('Profit (£)', ascending=False)
#             st.dataframe(display_fin.style.format({
#                 'Bought For (£)': '{:.1f}', 'Current Value (£)': '{:.1f}', 
#                 'Actual Sell Price (£)': '{:.1f}', 'Profit (£)': '{:.1f}'
#             }), use_container_width=True, hide_index=True)

#         # --- TAB 1 BOTTOM: AI ASSISTANT MANAGER ---
#         st.divider()
#         st.subheader("🤖 Chat with your Assistant Manager")

#         if not api_key:
#             st.info("🔑 Enter your Gemini API Key in the sidebar to activate the AI Assistant.")
#         else:
#             genai.configure(api_key=api_key)
#             model = genai.GenerativeModel(model_name='gemini-2.5-flash', tools='google_search')

#             if "messages" not in st.session_state: st.session_state.messages = []
#             for message in st.session_state.messages:
#                 with st.chat_message(message["role"]): st.markdown(message["content"])

#             user_question = st.chat_input("E.g., 'Is Saka injured? Should I bench him?'")

#             if user_question:
#                 with st.chat_message("user"): st.markdown(user_question)
#                 st.session_state.messages.append({"role": "user", "content": user_question})

#                 if my_team is not None:
#                     ai_starters, ai_bench = optimize_starting_lineup(my_team)
#                     gemini_history = []
#                     for msg in st.session_state.messages[:-1]: 
#                         gemini_role = "model" if msg["role"] == "assistant" else "user"
#                         gemini_history.append({"role": gemini_role, "parts": [msg["content"]]})

#                     hidden_prompt = f"""
#                     CURRENT TEAM CONTEXT:
#                     Bank: £{bank}m | Real Sale Value: £{sale_value}m
#                     Starters:
#                     {ai_starters[['Position', 'web_name', 'ep_next', 'next_3_fdr']].to_string(index=False)}
#                     Bench:
#                     {ai_bench[['Position', 'web_name', 'ep_next']].to_string(index=False)}
                    
#                     INSTRUCTIONS:
#                     You are an elite FPL Assistant Manager. Answer the user's question using the team data above.
#                     CRITICAL RULE: If the user asks about a player's real-world status (injuries, rotation, press conferences), use your Google Search tool to find live news before answering.
                    
#                     USER QUESTION: {user_question}
#                     """
#                     gemini_history.append({"role": "user", "parts": [hidden_prompt]})

#                     with st.chat_message("assistant"):
#                         with st.spinner("Searching the web and analyzing your squad..."):
#                             try:
#                                 response = model.generate_content(gemini_history)
#                                 st.markdown(response.text)
#                                 st.session_state.messages.append({"role": "assistant", "content": response.text})
#                             except Exception as e:
#                                 st.error(f"API Error: {str(e)}")
#                 else:
#                     with st.chat_message("assistant"): st.warning("⚠️ Please scroll up and click 'Analyze My Team' first!")

#     # --- TAB 2, 3, 4 (No changes needed) ---
#     with tab2:
#         st.header("🎯 Master Transfer Targets")
#         st.dataframe(targets_df, use_container_width=True, hide_index=True)

#     # with tab3:
#     #     st.header("🃏 Smart Wildcard Generator")
#     #     selected_budget = st.slider("Set Wildcard Budget (£m)", min_value=90.0, max_value=110.0, value=float(sale_value), step=0.1)
#     #     if st.button("Generate Wildcard Squad"):
#     #         with st.spinner("Crunching the numbers..."):
#     #             clean_targets = targets_df.dropna(subset=['now_cost', 'buy_rating']).copy()
#     #             clean_targets = clean_targets[clean_targets['ep_next'].astype(float) > 0.5]
#     #             wc_team = generate_wildcard(clean_targets, selected_budget)
#     #             wc_starters, wc_bench = optimize_starting_lineup(wc_team)
#     #             total_cost = round(wc_team['now_cost'].sum(), 1)
#     #             colA, colB = st.columns(2)
#     #             colA.metric("Total Squad Cost", f"£{total_cost}m")
#     #             st.dataframe(wc_starters[['Role', 'Position', 'web_name', 'starter_score']], use_container_width=True, hide_index=True)

#     # with tab4:
#     #     st.header("🔥 1-Week Free Hit Engine")
#     #     fh_budget = st.slider("Set Free Hit Budget (£m)", min_value=90.0, max_value=110.0, value=float(sale_value), step=0.1, key="fh_budget")
#     #     if st.button("Generate Free Hit Squad"):
#     #         with st.spinner("Optimizing purely for the upcoming Gameweek..."):
#     #             clean_targets = targets_df.dropna(subset=['now_cost', 'ep_next']).copy()
#     #             clean_targets = clean_targets[clean_targets['ep_next'].astype(float) > 0.5]
#     #             fh_team = generate_free_hit(clean_targets, fh_budget)
#     #             fh_starters, fh_bench = optimize_starting_lineup(fh_team)
#     #             total_fh_cost = round(fh_team['now_cost'].sum(), 1)
#     #             colA, colB = st.columns(2)
#     #             colA.metric("Total Squad Cost", f"£{total_fh_cost}m")
#     #             st.dataframe(fh_starters[['Role', 'Position', 'web_name', 'ep_next']], use_container_width=True, hide_index=True)

#     # --- TAB 3: SMART WILDCARD ---
#     with tab3:
#         st.header("🃏 Smart Wildcard Generator")
#         selected_budget = st.slider("Set Wildcard Budget (£m)", min_value=90.0, max_value=110.0, value=float(sale_value), step=0.1)
        
#         if st.button("Generate Wildcard Squad"):
#             with st.spinner("Crunching the numbers..."):
#                 clean_targets = targets_df.dropna(subset=['now_cost', 'buy_rating']).copy()
#                 clean_targets = clean_targets[clean_targets['ep_next'].astype(float) > 0.5]
                
#                 wc_team = generate_wildcard(clean_targets, selected_budget)
#                 wc_starters, wc_bench = optimize_starting_lineup(wc_team)
                
#                 total_cost = round(wc_team['now_cost'].sum(), 1)
#                 total_rating = round(wc_team['buy_rating'].sum(), 2)
                
#                 colA, colB = st.columns(2)
#                 colA.metric("Total Squad Cost", f"£{total_cost}m")
#                 colB.metric("Total Squad Rating", f"{total_rating} pts")
                
#                 st.subheader("📋 Optimal Wildcard Starting XI & Bench")
#                 st.markdown("**Starting 11 (Sorted by Match Rating + Captaincy)**")
#                 st.dataframe(wc_starters[['Role', 'Position', 'web_name', 'starter_score']], use_container_width=True, hide_index=True)
                
#                 # THE FIX: Added the Bench UI rendering back in!
#                 st.markdown("**Auto-Subs Bench (Optimal Order)**")
#                 st.dataframe(wc_bench[['Role', 'Position', 'web_name', 'starter_score']], use_container_width=True, hide_index=True)

#     # --- TAB 4: FREE HIT ENGINE ---
#     with tab4:
#         st.header("🔥 1-Week Free Hit Engine")
#         fh_budget = st.slider("Set Free Hit Budget (£m)", min_value=90.0, max_value=110.0, value=float(sale_value), step=0.1, key="fh_budget")
        
#         if st.button("Generate Free Hit Squad"):
#             with st.spinner("Optimizing purely for the upcoming Gameweek..."):
#                 clean_targets = targets_df.dropna(subset=['now_cost', 'ep_next']).copy()
#                 clean_targets = clean_targets[clean_targets['ep_next'].astype(float) > 0.5]
                
#                 fh_team = generate_free_hit(clean_targets, fh_budget)
#                 fh_starters, fh_bench = optimize_starting_lineup(fh_team)
                
#                 total_fh_cost = round(fh_team['now_cost'].sum(), 1)
#                 total_fh_ep = round(fh_team['ep_next'].sum(), 2)
                
#                 colA, colB = st.columns(2)
#                 colA.metric("Total Squad Cost", f"£{total_fh_cost}m")
#                 colB.metric("Total Squad Expected Points", f"{total_fh_ep} pts")
                
#                 st.subheader("📋 Optimal Free Hit Starting XI & Bench")
#                 st.markdown("**Starting 11 (Sorted by 1-Week Potential)**")
#                 st.dataframe(fh_starters[['Role', 'Position', 'web_name', 'ep_next']], use_container_width=True, hide_index=True)
                
#                 # THE FIX: Added the Bench UI rendering back in!
#                 st.markdown("**Auto-Subs Bench**")
#                 st.dataframe(fh_bench[['Role', 'Position', 'web_name', 'ep_next']], use_container_width=True, hide_index=True)

# --- 3. UI LAYOUT & INTERFACE ---
players_df, targets_df, gw_df = load_csv_data()

if players_df is not None:
    st.sidebar.header("🛠️ Manager Settings")
    manager_id_input = st.sidebar.text_input("Enter FPL Manager ID", "9478527")
    free_transfers = st.sidebar.number_input("Free Transfers Available", min_value=1, max_value=5, value=1)
    analyze_button = st.sidebar.button("Analyze My Team")
    
    # NEW TABS SETUP
    tab1, tab5, tab2, tab3, tab4 = st.tabs(["📊 My Team Analysis", "📅 Fixtures & Chips", "🎯 Master Target List", "🃏 Smart Wildcard", "🔥 1-Week Free Hit"])
    
    if "my_team" not in st.session_state:
        st.session_state.my_team = None
        st.session_state.bank = 0.0
        st.session_state.sale_value = 100.0
        st.session_state.total_value = 100.0
        st.session_state.available_chips = []
        st.session_state.gw = 1

    if analyze_button and manager_id_input.isdigit():
        manager_id = int(manager_id_input)
        gw = get_current_gameweek(gw_df)
        st.session_state.gw = gw
        
        manager_data = fetch_manager_data(manager_id, gw)
        st.session_state.available_chips = get_available_chips(manager_id)
        
        if manager_data:
            my_team_raw = manager_data["team"]
            st.session_state.bank = manager_data["bank"]
            
            # --- THE FINANCIAL MERGE ---
            squad_ids = my_team_raw['element'].tolist()
            financials = get_player_financials(manager_id, squad_ids)
            
            my_team_raw['purchase_price'] = my_team_raw['element'].map(lambda x: financials[x]['purchase_price'])
            my_team_raw['selling_price'] = my_team_raw['element'].map(lambda x: financials[x]['selling_price'])
            my_team_raw['profit'] = my_team_raw['element'].map(lambda x: financials[x]['profit'])
            
            my_team_temp = my_team_raw.merge(players_df[['id', 'web_name', 'now_cost', 'element_type', 'ep_next']], left_on='element', right_on='id')
            my_team_temp = my_team_temp.merge(targets_df[['web_name', 'buy_rating', 'next_3_fdr']], on='web_name', how='left')
            my_team_temp['buy_rating'] = my_team_temp['buy_rating'].fillna(0.0)
            
            st.session_state.total_value = round(my_team_temp['now_cost'].sum() + st.session_state.bank, 1)
            st.session_state.sale_value = round(my_team_temp['selling_price'].sum() + st.session_state.bank, 1)
            st.session_state.my_team = my_team_temp

    my_team = st.session_state.my_team
    bank = st.session_state.bank
    sale_value = st.session_state.sale_value
    total_value = st.session_state.total_value

    # --- TAB 1: TEAM ANALYSIS ---
    with tab1:
        if my_team is not None:
            st.success("✅ Data & Financials fetched successfully!")
            c1, c2, c3 = st.columns(3)
            c1.metric(label="💰 Money in the Bank", value=f"£{bank}m")
            c2.metric(label="📈 Squad Value (Raw)", value=f"£{total_value}m")
            c3.metric(label="📉 Real Sale Value", value=f"£{sale_value}m", help="This is your actual spending power after the 50% profit tax.")
            st.divider()
            
            col1, col2 = st.columns([1.5, 1])
            
            with col1:
                st.subheader("📋 Optimal Starting XI & Bench")
                starters, bench = optimize_starting_lineup(my_team)
                
                st.markdown("**Starting 11 (Sorted by Match Rating + Captaincy)**")
                st.dataframe(starters[['Role', 'Position', 'web_name', 'ep_next', 'next_3_fdr', 'starter_score']], use_container_width=True, hide_index=True)
                
                st.markdown("**Auto-Subs Bench (Optimal Order)**")
                st.dataframe(bench[['Role', 'Position', 'web_name', 'ep_next', 'starter_score']], use_container_width=True, hide_index=True)
                
            with col2:
                st.subheader("🔄 Transfer Optimizer")
                
                blanking_starters = starters[starters['ep_next'] <= 0]
                blanking_bench = bench[bench['ep_next'] <= 0]
                mids_and_fwds = my_team[(my_team['element_type_x'].isin([3, 4])) & (my_team['ep_next'] > 0)].sort_values('buy_rating', ascending=True)
                potential_sells = pd.concat([blanking_starters, blanking_bench, mids_and_fwds])
                
                current_bank = bank
                hyp_squad = my_team.copy()
                owned_names = hyp_squad['web_name'].tolist()
                transfers_made = 0
                
                for index, weakest_link in potential_sells.iterrows():
                    if transfers_made >= free_transfers: break
                        
                    sell_price = weakest_link['selling_price']
                    available_funds = round(current_bank + sell_price, 1)
                    
                    is_emergency = weakest_link['ep_next'] <= 0
                    sell_reason = "Emergency (Blank/Injured)" if is_emergency else "Lowest Squad Rating"
                    
                    affordable_targets = targets_df[
                        (~targets_df['web_name'].isin(owned_names)) & 
                        (targets_df['element_type'] == weakest_link['element_type_x']) &
                        (targets_df['now_cost'] <= available_funds) &
                        (targets_df['ep_next'].astype(float) > 0.5) 
                    ].sort_values('buy_rating', ascending=False)
                    
                    if not affordable_targets.empty:
                        top_replacement = affordable_targets.iloc[0]
                        point_swing = round(top_replacement['buy_rating'] - weakest_link['buy_rating'], 2)
                        
                        if is_emergency or point_swing > 0:
                            st.error(f"📉 **SELL:** {weakest_link['web_name']} (Sell Price: £{sell_price:.1f}m | Profit: £{weakest_link['profit']:.1f}m)")
                            st.caption(f"Reason: {sell_reason}")
                            st.success(f"📈 **BUY:** {top_replacement['web_name']} (£{top_replacement['now_cost']:.1f}m) - Rating: {round(top_replacement['buy_rating'], 2)}")
                            
                            if is_emergency: st.metric(label="Expected Rating Swing", value="FIXED SQUAD")
                            else: st.metric(label="Expected Rating Swing", value=f"+{point_swing}")
                            st.divider()
                            
                            current_bank = available_funds - top_replacement['now_cost']
                            owned_names.append(top_replacement['web_name'])
                            
                            hyp_squad = hyp_squad[hyp_squad['web_name'] != weakest_link['web_name']]
                            new_player = top_replacement.to_frame().T
                            new_player['element_type_x'] = new_player['element_type']
                            hyp_squad = pd.concat([hyp_squad, new_player], ignore_index=True)
                            
                            transfers_made += 1
                
                if transfers_made > 0:
                    with st.expander(f"🔮 View Squad After ALL {transfers_made} Transfer(s)", expanded=False):
                        hyp_starters, hyp_bench = optimize_starting_lineup(hyp_squad)
                        st.markdown("**New Starting 11 & Captaincy**")
                        st.dataframe(hyp_starters[['Role', 'Position', 'web_name', 'starter_score']], use_container_width=True, hide_index=True)
                else:
                    st.info("Hold your transfers! Your current squad mathematically outperforms affordable alternatives.")
            
            st.divider()
            st.subheader("💵 Squad Financial Tracker")
            st.write("Track exactly how much profit your players have made, and how much FPL will let you sell them for.")
            
            display_fin = my_team[['web_name', 'purchase_price', 'now_cost', 'selling_price', 'profit']].copy()
            display_fin.columns = ['Player', 'Bought For (£)', 'Current Value (£)', 'Actual Sell Price (£)', 'Profit (£)']
            display_fin = display_fin.sort_values('Profit (£)', ascending=False)
            st.dataframe(display_fin.style.format({
                'Bought For (£)': '{:.1f}', 'Current Value (£)': '{:.1f}', 
                'Actual Sell Price (£)': '{:.1f}', 'Profit (£)': '{:.1f}'
            }), use_container_width=True, hide_index=True)

    # # --- TAB 5: NEW FIXTURES & CHIP STRATEGY ---
    # with tab5:
    #     st.header("📅 Fixtures & Chips Strategy")
    #     if my_team is not None:
    #         col_chips, col_sched = st.columns([1, 2])
            
    #         with col_chips:
    #             st.subheader("🎒 Your Available Chips")
    #             if st.session_state.available_chips:
    #                 for chip in st.session_state.available_chips:
    #                     st.success(f"✅ {chip}")
    #             else:
    #                 st.error("❌ No chips remaining!")
            
    #         with col_sched:
    #             st.subheader("🔭 Next 4 Gameweeks Radar")
    #             with st.spinner("Scanning schedule for DGWs and BGWs..."):
    #                 density_report = get_fixture_density(st.session_state.gw, lookahead=4)
                    
    #                 if density_report:
    #                     for gw_data in density_report:
    #                         gw = gw_data['GW']
                            
    #                         if gw_data['Blanks'] or gw_data['Doubles']:
    #                             with st.expander(f"Gameweek {gw} Exceptions", expanded=True):
    #                                 if gw_data['Doubles']:
    #                                     st.markdown(f"**🟢 Doubles (Plays Twice):** {', '.join(gw_data['Doubles'])}")
    #                                 if gw_data['Blanks']:
    #                                     st.markdown(f"**🔴 Blanks (0 Fixtures):** {', '.join(gw_data['Blanks'])}")
    #                         else:
    #                             st.write(f"**Gameweek {gw}:** Standard fixtures (all teams play once).")
                                
    #         st.divider()
    #         st.subheader("💡 Strategic Advice")
    #         if density_report:
    #             advice = suggest_chip_strategy(density_report, st.session_state.available_chips)
    #             for line in advice:
    #                 st.markdown(line)
    #     else:
    #         st.info("⚠️ Please click 'Analyze My Team' in the sidebar to load your chip and fixture data.")

    # --- TAB 5: NEW FIXTURES & CHIP STRATEGY ---
    with tab5:
        st.header("📅 Rest-of-Season Fixtures & Chip Planner")
        if my_team is not None:
            col_chips, col_sched = st.columns([1, 2])
            
            with col_chips:
                st.subheader("🎒 Your Available Chips")
                if st.session_state.available_chips:
                    for chip in st.session_state.available_chips:
                        st.success(f"✅ {chip}")
                else:
                    st.error("❌ No chips remaining!")
                    
                st.divider()
                st.subheader("💡 Strategic Roadmap")
                density_report = get_fixture_density(st.session_state.gw, end_gw=38)
                if density_report:
                    advice = suggest_chip_strategy(density_report, st.session_state.available_chips)
                    for line in advice:
                        st.markdown(line)
            
            with col_sched:
                st.subheader("🔭 Gameweek Exception Radar (Rest of Season)")
                with st.spinner("Scanning all remaining schedules for DGWs and BGWs..."):
                    if density_report:
                        exceptions_found = False
                        for gw_data in density_report:
                            gw = gw_data['GW']
                            
                            if gw_data['Blanks'] or gw_data['Doubles']:
                                exceptions_found = True
                                with st.expander(f"Gameweek {gw} Exceptions", expanded=True):
                                    if gw_data['Doubles']:
                                        st.markdown(f"**🟢 Doubles (Plays Twice):** {', '.join(gw_data['Doubles'])}")
                                    if gw_data['Blanks']:
                                        st.markdown(f"**🔴 Blanks (0 Fixtures):** {', '.join(gw_data['Blanks'])}")
                        
                        if not exceptions_found:
                            st.info("No more Double or Blank Gameweeks currently scheduled for the rest of the season. All standard fixtures.")
        else:
            st.info("⚠️ Please click 'Analyze My Team' in the sidebar to load your chip and fixture data.")

    # --- TAB 2: MASTER TARGETS ---
    with tab2:
        st.header("🎯 Master Transfer Targets")
        st.dataframe(targets_df, use_container_width=True, hide_index=True)

    # --- TAB 3: SMART WILDCARD ---
    with tab3:
        st.header("🃏 Smart Wildcard Generator")
        selected_budget = st.slider("Set Wildcard Budget (£m)", min_value=90.0, max_value=110.0, value=float(sale_value), step=0.1)
        
        if st.button("Generate Wildcard Squad"):
            with st.spinner("Crunching the numbers..."):
                clean_targets = targets_df.dropna(subset=['now_cost', 'buy_rating']).copy()
                clean_targets = clean_targets[clean_targets['ep_next'].astype(float) > 0.5]
                
                wc_team = generate_wildcard(clean_targets, selected_budget)
                wc_starters, wc_bench = optimize_starting_lineup(wc_team)
                
                total_cost = round(wc_team['now_cost'].sum(), 1)
                total_rating = round(wc_team['buy_rating'].sum(), 2)
                
                colA, colB = st.columns(2)
                colA.metric("Total Squad Cost", f"£{total_cost}m")
                colB.metric("Total Squad Rating", f"{total_rating} pts")
                
                st.subheader("📋 Optimal Wildcard Starting XI & Bench")
                st.markdown("**Starting 11 (Sorted by Match Rating + Captaincy)**")
                st.dataframe(wc_starters[['Role', 'Position', 'web_name', 'starter_score']], use_container_width=True, hide_index=True)
                
                st.markdown("**Auto-Subs Bench (Optimal Order)**")
                st.dataframe(wc_bench[['Role', 'Position', 'web_name', 'starter_score']], use_container_width=True, hide_index=True)

    # --- TAB 4: FREE HIT ENGINE ---
    with tab4:
        st.header("🔥 1-Week Free Hit Engine")
        fh_budget = st.slider("Set Free Hit Budget (£m)", min_value=90.0, max_value=110.0, value=float(sale_value), step=0.1, key="fh_budget")
        
        if st.button("Generate Free Hit Squad"):
            with st.spinner("Optimizing purely for the upcoming Gameweek..."):
                clean_targets = targets_df.dropna(subset=['now_cost', 'ep_next']).copy()
                clean_targets = clean_targets[clean_targets['ep_next'].astype(float) > 0.5]
                
                fh_team = generate_free_hit(clean_targets, fh_budget)
                fh_starters, fh_bench = optimize_starting_lineup(fh_team)
                
                total_fh_cost = round(fh_team['now_cost'].sum(), 1)
                total_fh_ep = round(fh_team['ep_next'].sum(), 2)
                
                colA, colB = st.columns(2)
                colA.metric("Total Squad Cost", f"£{total_fh_cost}m")
                colB.metric("Total Squad Expected Points", f"{total_fh_ep} pts")
                
                st.subheader("📋 Optimal Free Hit Starting XI & Bench")
                st.markdown("**Starting 11 (Sorted by 1-Week Potential)**")
                st.dataframe(fh_starters[['Role', 'Position', 'web_name', 'ep_next']], use_container_width=True, hide_index=True)
                
                st.markdown("**Auto-Subs Bench**")
                st.dataframe(fh_bench[['Role', 'Position', 'web_name', 'ep_next']], use_container_width=True, hide_index=True)