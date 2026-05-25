import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import calendar
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import norm

# --- 1. RESTORE STATE FROM URL ON REFRESH ---
if "app_loaded" not in st.session_state:
    # If a parameter exists in the URL, inject it into the session state!
    if "ticker" in st.query_params:
        st.session_state.active_ticker = st.query_params["ticker"]
    if "view" in st.query_params:
        st.session_state.chart_view = int(st.query_params["view"])
    if "tf" in st.query_params:
        st.session_state.selected_tf = st.query_params["tf"]
    if "exp_dates" in st.query_params:
        st.session_state.selected_exps = st.query_params.get_all("exp_dates")
        
    st.session_state.app_loaded = True

# Ensure default states exist if they weren't in the URL
if "active_ticker" not in st.session_state:
    st.session_state.active_ticker = ""
if "chart_view" not in st.session_state:
    st.session_state.chart_view = 0
if "selected_tf" not in st.session_state:
    st.session_state.selected_tf = "5m"
if "selected_exps" not in st.session_state:
    st.session_state.selected_exps = [] 
    


# --- CSS: Hide Crosshair, Keep Axis Stretch Arrows ---
st.markdown(
    """
    <style>
    /* 1. Force the main chart cursor to be a normal arrow */
    .js-plotly-plot .plotly .cursor-crosshair {
        cursor: default !important;
    }
    
    /* 2. Force single line and adjust font to fit */
    .stMultiSelect [data-baseweb="tag"] {
        max-width: 100% !important;
    }
    .stMultiSelect [data-baseweb="tag"] span {
        white-space: nowrap !important;     /* Forces text to stay on one straight line */
        font-size: 12px !important;         /* Shrinks the font slightly so the full text fits */
        overflow: hidden !important;
        text-overflow: ellipsis !important; /* Adds '...' ONLY if a date is unusually long */
    }
    </style>
    """,
    unsafe_allow_html=True
)

# --- Number Formatter for Hover Text ---
def format_large_number(n):
    """Formats large numbers into M, B, T rounding to the nearest tenth."""
    if abs(n) >= 1e12:
        return f"{n/1e12:.1f}T"
    elif abs(n) >= 1e9:
        return f"{n/1e9:.1f}B"
    elif abs(n) >= 1e6:
        return f"{n/1e6:.1f}M"
    elif abs(n) >= 1e3:
        return f"{n/1e3:.1f}K"
    else:
        return f"{n:.1f}"

# --- Black-Scholes Gamma Calculation ---
def calculate_gamma(S, K, T, r, sigma):
    """Calculates Gamma using the standard Black-Scholes model."""
    if T <= 0 or sigma <= 0:
        return 0.0
    
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    return gamma

def format_exp_label(date_str, exp_set):
    exp_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    today = datetime.date.today()
    dte = (exp_date - today).days

    if date_str in exp_set:
        type_str = "(M)"
    else:
        type_str = "(W)"

    return f"{date_str} ({dte} DTE) {type_str}"

def get_true_monthlies(available_dates):
    # Convert string dates to datetime objects for math
    parsed_dates = [datetime.datetime.strptime(d, '%Y-%m-%d').date() for d in available_dates]
    true_monthlies = set()

    # Find every unique Year/Month combo in the options chain
    months_seen = set((d.year, d.month) for d in parsed_dates)

    for year, month in months_seen:
        # Calculate the dates of all Fridays in this specific month
        cal = calendar.monthcalendar(year, month)
        fridays = [week[4] for week in cal if week[4] != 0]
        
        # Grab the 3rd Friday
        third_friday_day = fridays[2]
        third_friday = datetime.date(year, month, third_friday_day)

        if third_friday in parsed_dates:
            # Standard month: The 3rd Friday is open for trading
            true_monthlies.add(third_friday.strftime('%Y-%m-%d'))
        else:
            # Holiday month: Check if the Thursday right before it exists in the chain
            thursday_before = third_friday - datetime.timedelta(days=1)
            if thursday_before in parsed_dates:
                true_monthlies.add(thursday_before.strftime('%Y-%m-%d'))

    return true_monthlies

# --- SPX/SPY Ratio Converter ---
def get_spx_spy_ratio():
    try:
        spx_live = float(yf.Ticker("^SPX").history(period="1d")['Close'].iloc[-1])
        spy_live = float(yf.Ticker("SPY").history(period="1d")['Close'].iloc[-1])
        # Calculate the exact dollar gap between SPX and 10x SPY
        offset_gap = spx_live - (spy_live * 10)
        return offset_gap, spx_live, spy_live
    except Exception:
        return 0.0, 5000.0, 500.0

# --- App Layout & Logic ---
def make_uppercase():
    st.session_state.ticker_input = st.session_state.ticker_input.upper()

st.sidebar.header("Settings")

if 'active_ticker' not in st.session_state:
    st.session_state.active_ticker = ""

def submit_ticker():
    # Grab the typed text, make it uppercase, and save it to memory
    new_symbol = st.session_state.ticker_search_box.strip().upper()
    if new_symbol:
        st.session_state.active_ticker = new_symbol
    st.session_state.ticker_search_box = ""
    # Save the new ticker to the URL param
    st.query_params["ticker"] = st.session_state.active_ticker

def update_timeframe(new_tf):
    # This runs the exact millisecond the button is clicked!
    st.session_state.selected_tf = new_tf
    st.query_params["tf"] = new_tf

def update_exp_dates():
    # Grab the current list of dates directly from the widget's memory key
    new_dates = st.session_state.exp_widget
    # Update the permanent session state and write the list to the URL
    st.session_state.selected_exps = new_dates
    st.query_params["exp_dates"] = new_dates


st.sidebar.text_input(
    "Enter Ticker Symbol", 
    key="ticker_search_box", 
    placeholder="e.g., ^SPX, SPY, AAPL",
    on_change=submit_ticker
)

ticker_input = st.session_state.active_ticker

st.sidebar.markdown("<br>", unsafe_allow_html=True) # Adds a little spacing

def swap_converter_direction():
    if st.session_state.conv_dir == "SPY_TO_SPX":
        st.session_state.conv_dir = "SPX_TO_SPY"
    else:
        st.session_state.conv_dir = "SPY_TO_SPX"


# SPX <-> SPY Converter
def render_converter():
    offset_gap, spx_live, spy_live = get_spx_spy_ratio()

    # 1. Initialize the direction state and target memory
    if "conv_dir" not in st.session_state:
        st.session_state.conv_dir = "SPY_TO_SPX" 
    if "calc_target_spx" not in st.session_state:
        st.session_state.calc_target_spx = spx_live
    if "calc_target_spy" not in st.session_state:
        st.session_state.calc_target_spy = spy_live

    # 2. Callback function to instantly swap the active direction
    with st.sidebar.popover("SPX ↔ SPY Converter", use_container_width=True):
        col_in, col_btn, col_out = st.columns([4, 2, 4], vertical_alignment="bottom")
        
        with col_btn:
            st.markdown(
                """
                <style>
                button[kind="tertiary"] p { font-size: 36px !important; line-height: 0 !important; }
                [data-testid="stMetric"] { margin-top: 16px !important; }
                </style>
                """,
                unsafe_allow_html=True
            )
            st.button("↔️", on_click=swap_converter_direction, use_container_width=True, key="swap_btn", type="tertiary")

        # --- CONCISE DYNAMIC UI LOGIC ---
        is_spy_base = st.session_state.conv_dir == "SPY_TO_SPX"
        
        # Dynamically assign labels, keys, and step sizes in one block
        in_lbl = "**SPY**" if is_spy_base else "**SPX**"
        out_lbl = "**SPX**" if is_spy_base else "**SPY Equiv**"
        step_val = 1.0 if is_spy_base else 10.0
        in_key = "calc_target_spy" if is_spy_base else "calc_target_spx"

        # Render the UI exactly once
        with col_in:
            target_val = st.number_input(in_lbl, step=step_val, format="%.2f", key=in_key)
            
        with col_out:
            # Calculate the pure math inline
            equiv_val = (target_val * 10.0) + offset_gap if is_spy_base else (target_val - offset_gap) / 10.0
            st.metric(label=out_lbl, value=f"${equiv_val:.2f}")

        st.markdown(f"<div style='text-align: center; font-size: 13px; color: #00E676; margin-bottom: 15px;'>Live Dividend Gap: {offset_gap:.2f} pts</div>", unsafe_allow_html=True)

render_converter()
st.sidebar.divider()

st.set_page_config(page_title="Universal GEX Dashboard", layout="wide")
st.title(f"{ticker_input} Gamma Exposure (GEX) Profile")

# 2. Fetch Ticker Data
@st.cache_data(ttl=60) 
def get_ticker_data(ticker):
    try:
        temp_ticker = yf.Ticker(ticker)
        raw_price_df = temp_ticker.history(period='1mo', interval='5m')

        # Pull extended hours for the sidebar display
        hist_ext = temp_ticker.history(period="1d", interval="1m", prepost=True)
        
        spot_price = float(raw_price_df['Close'].iloc[-1])
        
        # If extended hours exist, grab the latest tick. Otherwise, default to spot.
        ext_price = float(hist_ext['Close'].iloc[-1]) if not hist_ext.empty else spot_price

        expirations = list(temp_ticker.options)
        return spot_price, ext_price, raw_price_df, expirations
    except Exception as e:
        return None, None, None, []
    

# --- 2. THE IN-MEMORY RESAMPLER ---
def resample_timeframe(raw_df, timeframe):
    # If the user wants 5m data, just hand them the raw dataframe
    if timeframe == "5m" or raw_df.empty:
        return raw_df
        
    # Map your UI labels to Pandas timeframe aliases
    resample_map = {
        "15m": "15min", # Must use 'min' instead of 'm' for Pandas
        "30m": "30min",
        "1h": "1h"
    }
    pandas_tf = resample_map.get(timeframe, "15min")
    
    # Standard market rules for building a larger candle from smaller ones
    aggregation_rules = {
        'Open': 'first', # The open is the first tick of the period
        'High': 'max',   # The high is the highest tick of the period
        'Low': 'min',    # The low is the lowest tick of the period
        'Close': 'last', # The close is the last tick of the period
        'Volume': 'sum'  # Volume is added all together
    }
    
    # Group the data, apply the rules, and drop any empty chunks (like overnight hours)
    resampled_df = raw_df.resample(pandas_tf, offset='30min').agg(aggregation_rules).dropna()
    return resampled_df

if ticker_input:
    spot_price, ext_price, raw_price_df, expirations = get_ticker_data(ticker_input)
    price_df = resample_timeframe(raw_price_df, st.session_state.selected_tf)
    
    if spot_price is None or not expirations:
        st.error(f"❌ Could not retrieve options data for '{ticker_input}'. Please verify the ticker symbol.")
    else:
        st.sidebar.metric(label=f"{ticker_input} Spot Price", value=f"${spot_price:.2f}")

        st.sidebar.markdown(
            f"<div style='margin-top: -15px; margin-bottom: 20px; font-size: 14px; color: #a5a5a5;'>"
            f"Extended Hours: ${ext_price:.2f}</div>", 
            unsafe_allow_html=True
        )

        ticker_obj = yf.Ticker(ticker_input)
        true_monthlies_set = get_true_monthlies(expirations)

        # --- THE TRANSLATION DICTIONARY ---
        # Map the pretty labels to the raw YYYY-MM-DD strings
        display_options = [format_exp_label(exp, true_monthlies_set) for exp in expirations]
        exp_mapping = {format_exp_label(exp, true_monthlies_set): exp for exp in expirations}
        
        # Check which of the previously selected dates actually exist for this new ticker
        valid_defaults = [exp for exp in st.session_state.selected_exps if exp in display_options]
        
        # Failsafe: If none match (or it's the very first time loading), pick the nearest expiration
        if not valid_defaults and display_options:
            valid_defaults = [display_options[0]]

        if 'exp_widget' in st.session_state:
            if any(val not in display_options for val in st.session_state.exp_widget):
                del st.session_state['exp_widget']

        # Inject the validated defaults into the widget's native state BEFORE it renders
        if 'exp_widget' not in st.session_state:
            st.session_state.exp_widget = valid_defaults

        selected_exps = st.sidebar.multiselect(
            "Select Expiration Date(s)", 
            options=display_options,
            max_selections=4,
            key="exp_widget",
            on_change=update_exp_dates
        )
    
        raw_selected_exps = [exp_mapping[label] for label in selected_exps]
        title_dates = ", ".join(raw_selected_exps)

        # 3. Process Options Chains 
        if selected_exps:
            raw_selected_exps = [exp_mapping[label] for label in selected_exps]
            all_dfs = []
            
            # Loop through each selected expiration
            for exp in raw_selected_exps:
                chain = ticker_obj.option_chain(exp)
                calls = chain.calls
                puts = chain.puts
                
                if calls.empty and puts.empty:
                    st.warning(f"⚠️ Yahoo Finance returned empty data for {exp}. Skipping.")
                    continue

                calls['openInterest'] = calls['openInterest'].fillna(0)
                puts['openInterest'] = puts['openInterest'].fillna(0)
                calls['impliedVolatility'] = calls['impliedVolatility'].fillna(0.0001)
                puts['impliedVolatility'] = puts['impliedVolatility'].fillna(0.0001)

                exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                today = datetime.date.today()
                days_to_exp = (exp_date - today).days
                T = max(days_to_exp / 365.0, 1 / 365.0) 
                r = 0.053 

                calls['Gamma'] = calls.apply(lambda row: calculate_gamma(spot_price, row['strike'], T, r, row['impliedVolatility']), axis=1)
                calls['Call GEX'] = calls['openInterest'] * calls['Gamma'] * 100 * spot_price
                calls['Type'] = 'Call'

                puts['Gamma'] = puts.apply(lambda row: calculate_gamma(spot_price, row['strike'], T, r, row['impliedVolatility']), axis=1)
                puts['Put GEX'] = puts['openInterest'] * puts['Gamma'] * 100 * spot_price * -1
                puts['Type'] = 'Put'

                # Group by 'strike' to aggregate all expirations together
                call_gex = calls.groupby('strike')['Call GEX'].sum().reset_index()
                put_gex = puts.groupby('strike')['Put GEX'].sum().reset_index()

                # Merge them together into one DataFrame
                net_gex = pd.merge(call_gex, put_gex, on='strike', how='outer').fillna(0)

                # 5. Calculate the Final Net GEX
                net_gex['Net GEX'] = net_gex['Call GEX'] + net_gex['Put GEX']

                # Add processed calls and puts for this specific expiration to our master list
                all_dfs.extend([calls, puts, net_gex])
            
            # --- AGGREGATION ---
            if not all_dfs:
                st.warning(f"⚠️ Yahoo Finance returned empty options data for '{ticker_input}' on the selected dates.")
                st.stop()
            else:

                if 'chart_view' not in st.session_state:
                    st.session_state.chart_view = 0 # 0: 3-pane, 1: Put/Call GEX, 2: Net GEX

                def cycle_view():
                    st.session_state.chart_view = (st.session_state.chart_view + 1) % 3
                    # Save the new view to the URL params
                    st.query_params["view"] = str(st.session_state.chart_view)

                # DYNAMIC UI ROUTING
                next_view_labels = [
                    "➡️ Put/Call GEX", # If currently on View 0
                    "➡️ Net GEX",      # If currently on View 1
                    "➡️ 3-Pane Order Flow"        # If currently on View 2
                ]

                # Button right-aligned using a single column
                btn_col = st.columns([0.7, 0.3])[1]
                with btn_col:
                    st.button(
                        next_view_labels[st.session_state.chart_view], 
                        on_click=cycle_view, 
                        width='stretch'
                    )

                # --- CALCULATE GAMMA FLIP ---
                try:
                    # 1. Sort by strike to ensure sequential order
                    temp_df = net_gex.sort_values('strike').copy()
                    
                    # 2. Filter out strikes with exactly 0 GEX (removes deep OTM noise)
                    temp_df = temp_df[temp_df['Net GEX'] != 0]
                    
                    # 3. Detect zero-crossings (where the sign changes from + to - or - to +)
                    temp_df['sign'] = np.sign(temp_df['Net GEX'])
                    temp_df['sign_shift'] = temp_df['sign'].diff()
                    
                    # 4. Isolate only the strikes where a transition occurred
                    flips = temp_df[temp_df['sign_shift'] != 0].dropna()
                    
                    if not flips.empty:
                        # 5. Find the specific flip that is closest to the current Spot Price
                        gamma_flip = flips.iloc[(flips['strike'] - spot_price).abs().argmin()]['strike']
                    else:
                        gamma_flip = spot_price
                except Exception:
                    gamma_flip = spot_price # Safe fallback
                    
                
                # --- DEFAULT STARTING ZOOM FOR VIEW 0 ---
                try:
                    day_low = price_df['Low'].min()
                    day_high = price_df['High'].max()
                except Exception:
                    day_low = spot_price * 0.99
                    day_high = spot_price * 1.01
                
                price_range = day_high - day_low
                padding = price_range * 0.15 if price_range > 0 else (spot_price * 0.005)
                y_min = day_low - padding
                y_max = day_high + padding


                # --- CALCULATE WIDE ZOOM FOR VIEWS 1 & 2 ---
                # Filter for strikes that actually have some GEX to ignore empty rows
                active_gex = net_gex[(net_gex['Call GEX'].abs() > 0) | (net_gex['Put GEX'].abs() > 0)]
                
                if not active_gex.empty:
                    # Grab the 7.5th and 92.5th percentiles to perfectly frame ~85% of the active strikes
                    gex_x_min = active_gex['strike'].quantile(0.075)
                    gex_x_max = active_gex['strike'].quantile(0.925)
                else:
                    # Fallback just in case the dataframe is empty
                    gex_x_min = spot_price * 0.7 
                    gex_x_max = spot_price * 1.3


                # ROUTER LOGIC
                if st.session_state.chart_view == 0:
                    # ==========================================
                    # VIEW 0: THE 3-PANE VERTICAL ORDER FLOW
                    # ==========================================

                    # Create a row of buttons for timeframe selection
                    tf_col, empty_col = st.columns([2, 5]) 
    
                    with tf_col:
                        tf_options = ["5m", "15m", "30m", "1h"]
                        # Create a nested row of 4 equally-spaced columns just for the buttons
                        btn_cols = st.columns(len(tf_options))
                        
                        for i, tf in enumerate(tf_options):
                            with btn_cols[i]:
                                # Dynamically set the style: "primary" highlights the active one!
                                btn_style = "primary" if tf == st.session_state.selected_tf else "secondary"
                                
                                # THE FIX: Use on_click and args to update the URL flawlessly
                                st.button(
                                    label=tf, 
                                    type=btn_style, 
                                    use_container_width=True, 
                                    key=f"btn_tf_{tf}",
                                    on_click=update_timeframe, # Points to our new function
                                    args=(tf,)                 # Passes the string (e.g., "15m") to the function
                                )
                            
                    fig = make_subplots(
                        rows=1, cols=3,
                        shared_yaxes=True, 
                        column_widths=[0.5, 0.22, 0.23], 
                        horizontal_spacing=0.06, # Extremely tight spacing to fuse the charts together
                        subplot_titles=("", "Put/Call GEX", "Net GEX")
                    )

                    # --- ADD DAY DIVIDER LINES ---
                    # 1. Create a temporary column with just the dates
                    temp_df = price_df.copy()
                    temp_df['JustDate'] = temp_df.index.date
                    first_candles_of_day = temp_df.drop_duplicates(subset=['JustDate'])
                    
                    for start_time in first_candles_of_day.index:
                        fig.add_trace(
                            go.Scatter(
                                x=[start_time, start_time], 
                                y=[0, 9999999], 
                                mode="lines",
                                line=dict(dash="dot", color="rgba(128, 130, 140, 0.5)", width=1.5), 
                                showlegend=False,
                                hoverinfo="skip"
                            ),
                            row=1, col=1
                        )

                    # --- PANE 1: Candlestick Chart (Row 1, Col 1) ---
                    fig.add_trace(
                        go.Candlestick(
                            x=price_df.index,
                            open=price_df['Open'],
                            high=price_df['High'],
                            low=price_df['Low'],
                            close=price_df['Close'],
                            name="Price",
                            increasing=dict(line=dict(color='#26A69A', width=1.5), fillcolor='#26A69A'),
                            decreasing=dict(line=dict(color='#EF5350', width=1.5), fillcolor='#EF5350'),
                            showlegend=False,
                        ),
                        row=1, col=1
                    )

                    # --- PANE 2: Split Put / Call GEX (Row 1, Col 2) ---
                    fig.add_trace(
                        go.Bar(
                            x=all_dfs[2]['Call GEX'], 
                            y=all_dfs[2]['strike'],
                            orientation='h',
                            marker_color='#2196F3',
                            name="Call GEX",
                            hovertemplate='<b>Strike:</b> %{y}<br><b>Call GEX:</b> %{x:,.0f}<extra></extra>'
                        ),
                        row=1, col=2
                    )
                    
                    fig.add_trace(
                        go.Bar(
                            x=all_dfs[2]['Put GEX'], 
                            y=all_dfs[2]['strike'],
                            orientation='h',
                            marker_color='#FF9800',
                            name="Put GEX",
                            hovertemplate='<b>Strike:</b> %{y}<br><b>Put GEX:</b> %{x:,.0f}<extra></extra>'
                        ),
                        row=1, col=2
                    )

                    # --- PANE 3: Vertical Net GEX Profile (Row 1, Col 3) ---
                    net_gex_colors = ['#2196F3' if val >= 0 else '#FF9800' for val in all_dfs[2]['Net GEX']]

                    fig.add_trace(
                        go.Bar(
                            x=all_dfs[2]['Net GEX'],   
                            y=all_dfs[2]['strike'],    
                            orientation='h',        
                            marker_color=net_gex_colors,
                            name="Net GEX",
                            hovertemplate='<b>Strike:</b> %{y}<br><b>Net GEX:</b> %{x:,.0f}<extra></extra>'
                        ),
                        row=1, col=3
                    )

                    # --- FORMATTING AND AXIS SYNCING ---
                    # Global Spot Price Line (Cuts across ALL THREE panes natively)
                    fig.add_hline(
                        y=spot_price, 
                        line_dash="dash", 
                        line_color="rgba(30, 144, 255, 0.7)",
                        annotation_text=f" Spot: ${spot_price:.2f} ", 
                        annotation_position="top right", 
                        row=1, col=1) # Annotation only sits on the price pane so it doesn't overlap data

                    # --- CALCULATE CANDLESTICK SPACING ---
                    default_visible_candles = 100
                    
                    # 2. Create a temporary "view window" of just the most recent data
                    if len(price_df) > default_visible_candles:
                        view_df = price_df.iloc[-default_visible_candles:]
                    else:
                        view_df = price_df
                        
                    # 3. Set the X-Axis bounds to only span our clean view window
                    x_start = view_df.index[0]
                    
                    # Calculate the exact time gap between candles to add 5 "blank" candles of padding to the right
                    if len(view_df) >= 2:
                        candle_width = view_df.index[-1] - view_df.index[-2]
                    else:
                        candle_width = pd.Timedelta(minutes=5)
                        
                    x_end_padded = view_df.index[-1] + (candle_width * 5)
                    
                    # 4. Set the Y-Axis bounds strictly based on the visible window, NOT the whole month!
                    # We multiply by 0.998 and 1.002 to add a perfect 0.2% vertical margin so the wicks don't touch the ceiling/floor
                    y_min = view_df['Low'].min() * 0.998
                    y_max = view_df['High'].max() * 1.002

                    # --- 1. CORE LAYOUT & SHADING ---
                    fig.update_layout(
                        height=600, 
                        template="plotly_dark",
                        showlegend=False,
                        barmode='relative', 
                        bargap=0.5, 
                        dragmode='pan',
                        paper_bgcolor="rgba(0,0,0,0)", 
                        plot_bgcolor="rgba(255, 255, 255, 0.03)", 
                        margin=dict(l=60, r=40, t=90, b=80, pad=4), 
                        
                        # --- INDIVIDUAL AXIS SETTINGS ---
                        yaxis=dict(range=[y_min, y_max], title="Price / Strike", fixedrange=False, automargin=True, minallowed=0),
                        # --- Hide weekends and extended hours! ---
                        xaxis=dict(
                            range=[x_start, x_end_padded], 
                            rangeslider=dict(visible=False), 
                            automargin=True,
                            rangebreaks=[
                                dict(bounds=["sat", "mon"]),           # Hides the entire weekend
                                dict(bounds=[16, 9.25], pattern="hour") # Hides 4:00 PM to 9:30 AM every night
                            ]
                        ),
                        xaxis2=dict(title="Call / Put GEX", showgrid=True, zeroline=True, zerolinecolor='white', zerolinewidth=1, fixedrange=False, automargin=True, tickformat='.1s'), 
                        xaxis3=dict(title="Net GEX", showgrid=True, zeroline=True, zerolinecolor='white', zerolinewidth=1, fixedrange=False, automargin=True, tickformat='.1s')
                    )

                    # --- 3. BORDERING (THE MAGIC TRICK) ---
                    border_style = dict(showline=True, linewidth=1, linecolor='rgba(255, 255, 255, 0.15)', mirror=True)
                    
                    fig.update_xaxes(**border_style)
                    fig.update_yaxes(**border_style)

                    st.plotly_chart(fig, width='stretch', config={'scrollZoom': True})
                else:
                    # ==========================================
                    # VIEWS 1 & 2: HORIZONTAL CHARTS + TABLE
                    # ==========================================

                    fig = go.Figure()
                    
                    # 1. Define the specific traces and titles based on the view state
                    if st.session_state.chart_view == 1:
                        chart_title = "Put/Call Gamma Exposure by Strike"
                        y_title = "Gamma Exposure ($)"
                        table_cols = ['strike', 'Call GEX', 'Put GEX', 'Net GEX']
                        
                        fig.add_trace(go.Bar(x=net_gex['strike'], y=net_gex['Call GEX'], name='Call GEX', marker_color='rgba(0, 230, 118, 0.8)'))
                        fig.add_trace(go.Bar(x=net_gex['strike'], y=net_gex['Put GEX'], name='Put GEX', marker_color='rgba(255, 82, 82, 0.8)'))
                        fig.update_layout(barmode='relative')
                        
                    else: # View 2
                        chart_title = "Net Gamma Exposure by Strike"
                        y_title = "Net Gamma Exposure ($)"
                        table_cols = ['strike', 'Net GEX']
                        
                        net_colors = ['rgba(0, 230, 118, 0.8)' if x > 0 else 'rgba(255, 82, 82, 0.8)' for x in net_gex['Net GEX']]
                        fig.add_trace(go.Bar(x=net_gex['strike'], y=net_gex['Net GEX'], name='Net GEX', marker_color=net_colors))

                    # Spot price vertical line
                    fig.add_vline(
                        x=spot_price, 
                        line_dash="dash", 
                        line_color="blue", 
                        opacity=0.8,
                        annotation_text=f"Spot: ${spot_price:.2f}", 
                        annotation_font_color="blue",
                        annotation_position="top"
                    )

                    # Gamma flip vertical line       
                    fig.add_vline(
                        x=gamma_flip, 
                        line_dash="dot", 
                        line_color="#FFA000", 
                        opacity=0.8,
                        annotation_text=f"Gamma Flip: ${gamma_flip:.0f}", 
                        annotation_font_color="#FFA000",
                        annotation_position="bottom left"
                    )

                    # --- NEW BACKGROUND SHADING ---
                    # Create massive bounds so the shading doesn't break if the user zooms all the way out
                    abs_min_strike = net_gex['strike'].min() * 0.1
                    abs_max_strike = net_gex['strike'].max() * 2.0

                    # Shade the "Below Flip" area (Light Orange)
                    fig.add_vrect(
                        x0=abs_min_strike, x1=gamma_flip, 
                        fillcolor="rgba(255, 160, 0, 0.1)", # 6% opacity orange
                        layer="below", line_width=0 
                    )

                    # Shade the "Above Flip" area (Light Green)
                    fig.add_vrect(
                        x0=gamma_flip, x1=abs_max_strike, 
                        fillcolor="rgba(0, 230, 118, 0.1)", # 5% opacity green
                        layer="below", line_width=0
                    )

                    # We use invisible scatter plots with square markers to force the custom colors into the legend.
                    # Note: We bump the opacity from 0.05 up to 0.5 here so the squares are actually visible in the legend box!
                    fig.add_trace(go.Scatter(
                        x=[None], y=[None],
                        mode='markers',
                        marker=dict(size=15, color="rgba(0, 230, 118, 0.1)", symbol='square'),
                        name="Positive GEX Regime"
                    ))
                    
                    fig.add_trace(go.Scatter(
                        x=[None], y=[None],
                        mode='markers',
                        marker=dict(size=15, color="rgba(255, 160, 0, 0.1)", symbol='square'),
                        name="Negative GEX Regime"
                    ))

                    fig.update_layout(
                        showlegend=True,
                        legend=dict(
                            orientation="v",       # Vertical stack
                            yanchor="top",
                            y=1.0,                 # Align with the top of the chart
                            xanchor="left",
                            x=1.02                 # Push slightly outside the right edge of the grid
                        ),
                        title=chart_title,
                        template="plotly_dark",
                        xaxis=dict(title="Strike Price", range=[gex_x_min, gex_x_max], fixedrange=False, type="linear", minallowed=0),
                        yaxis=dict(title=y_title, fixedrange=False, type="linear")
                    )
                        
                    st.plotly_chart(fig, width='stretch', config={'scrollZoom': True, 'displayModeBar': True})
                    st.markdown("### GEX Data Table")
                    st.dataframe(net_gex[table_cols], width='stretch')

else:
    # --- DEFAULT SCREEN ---
    # It forces the main container to take up exactly 100% of the screen height
    # and vertically/horizontally centers everything inside it.
    st.markdown(
        """
        <style>
        /* Target the main Streamlit container */
        [data-testid="stAppViewBlockContainer"] {
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            height: 100vh; 
            padding-top: 0rem !important;
            padding-bottom: 0rem !important;
            overflow: hidden; /* Kills the main page scrollbar */
        }
        
        /* Force the title text to center align */
        [data-testid="stMarkdownContainer"] h1 {
            text-align: center;
        }

        /* Constrain the image height so it never pushes the title off-screen */
        [data-testid="stImage"] img {
            max-height: 55vh; 
            object-fit: contain;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    try:
        # Try to load your local saved image
        st.image("images/gex_cover.png", width="stretch")
    except Exception:
        # Fallback to the sleek web image if the local file isn't found
        st.image("https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?q=80&w=1200&auto=format&fit=crop", width="stretch")

    st.markdown(
        "<p style='text-align: center; font-size: 18px; color: #a5a5a5; margin-top: 20px;'>"
        "Please enter a ticker symbol in the sidebar search box to generate a GEX profile."
        "</p>", 
        unsafe_allow_html=True
    )
            