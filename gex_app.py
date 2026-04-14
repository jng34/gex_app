from scipy.stats import norm
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.express as px
import datetime
import calendar

# --- CSS Hack: Hide Crosshair, Keep Axis Stretch Arrows ---
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

# --- Expiration Label Generator ---
def get_expiration_label(exp_str):
    """Calculates DTE and determines if it is a Monthly (3rd Friday) or Weekly."""
    exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    today = datetime.date.today()
    dte = (exp_date - today).days

    # Find the 3rd Friday of the month
    month_cal = calendar.monthcalendar(exp_date.year, exp_date.month)
    fridays = [week[4] for week in month_cal if week[4] != 0]
    third_friday = fridays[2] if len(fridays) >= 3 else None

    # If the expiration day matches the 3rd Friday, it's a Monthly
    if exp_date.day == third_friday:
        type_str = "(M)"
    else:
        type_str = "(W)"

    return f"{exp_str} ({dte} DTE) {type_str}"


# --- Auto-Select Text on Focus ---
def enable_auto_select():
    st.html(
        """
        <script>
        const parentDoc = window.parent.document;
        
        const selectOnFocus = () => {
            parentDoc.querySelectorAll('input[type="text"]').forEach((input) => {
                // Check if we already added the listener to prevent duplicates
                if (!input.dataset.hasSelectListener) {
                    input.addEventListener('focus', () => input.select());
                    input.dataset.hasSelectListener = 'true';
                }
            });
        };

        // Run once immediately
        selectOnFocus();

        // Keep watching for React re-renders
        const observer = new MutationObserver(selectOnFocus);
        observer.observe(parentDoc.body, { childList: true, subtree: true });
        </script>
        """,
        unsafe_allow_javascript=True 
    )

# Call the function to activate it
enable_auto_select()

# --- App Layout & Logic ---
def make_uppercase():
    st.session_state.ticker_input = st.session_state.ticker_input.upper()

# Initialize session state for ticker input
if 'ticker_input' not in st.session_state:
    st.session_state.ticker_input = "SPY"

# 1. Sidebar Inputs
st.sidebar.header("Settings")
# Allow user to input any ticker, default to SPY, and force uppercase
ticker_input = st.sidebar.text_input("Enter Ticker Symbol", key="ticker_input", on_change=make_uppercase)

st.set_page_config(page_title="Universal GEX Dashboard", layout="wide")
st.title(f"{ticker_input} Gamma Exposure (GEX) Profile")

# 2. Fetch Ticker Data
@st.cache_data(ttl=60) 
def get_ticker_data(ticker_symbol):
    try:
        temp_ticker = yf.Ticker(ticker_symbol)
        hist_reg = temp_ticker.history(period="1d")

        # Pull extended hours for the sidebar display
        hist_ext = temp_ticker.history(period="1d", interval="1m", prepost=True)
        
        if hist_reg.empty:
            return None, None, []
            
        spot_price = float(hist_reg['Close'].iloc[-1])
        
        # If extended hours exist, grab the latest tick. Otherwise, default to spot.
        ext_price = float(hist_ext['Close'].iloc[-1]) if not hist_ext.empty else spot_price

        expirations = list(temp_ticker.options)
        return spot_price, ext_price, expirations
    except Exception as e:
        return None, None, []

if ticker_input:
    spot_price, ext_price, expirations = get_ticker_data(ticker_input)
    
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

        # --- THE TRANSLATION DICTIONARY ---
        # Map the pretty labels to the raw YYYY-MM-DD strings
        exp_mapping = {get_expiration_label(exp): exp for exp in expirations}
        display_options = list(exp_mapping.keys())
        
        selected_exps = st.sidebar.multiselect(
            "Select Expiration Date(s)", 
            display_options, 
            default=[display_options[0]] if display_options else None,
            max_selections=4
        )

        # 3. Process Options Chains
        if selected_exps:
            # Translate the user's pretty selection back to the raw dates yfinance needs
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
                calls['GEX'] = calls['openInterest'] * calls['Gamma'] * 100 * spot_price
                calls['Type'] = 'Call'

                puts['Gamma'] = puts.apply(lambda row: calculate_gamma(spot_price, row['strike'], T, r, row['impliedVolatility']), axis=1)
                puts['GEX'] = puts['openInterest'] * puts['Gamma'] * 100 * spot_price * -1
                puts['Type'] = 'Put'

                # Add processed calls and puts for this specific expiration to our master list
                all_dfs.extend([calls, puts])
            
            # --- AGGREGATION ---
            if not all_dfs:
                st.warning("⚠️ No valid data could be processed for the selected dates.")
            else:
                # Smash all the expirations together into one massive dataset
                df = pd.concat(all_dfs, ignore_index=True)

                lower_bound = spot_price * 0.85
                upper_bound = spot_price * 1.15

                df_filtered = df[(df['strike'] >= lower_bound) & (df['strike'] <= upper_bound)]
                df_filtered = df_filtered.dropna(subset=['GEX'])
                
                # --- NEW FEATURE: Group by Strike AND Type for the colored bars ---
                gex_breakdown = df_filtered.groupby(['strike', 'Type'])['GEX'].sum().reset_index()
                gex_breakdown['GEX_Formatted'] = gex_breakdown['GEX'].apply(format_large_number)

                # --- BACKGROUND MATH: Still calculate Net GEX for the Gamma Flip line ---
                net_gex = df_filtered.groupby('strike')['GEX'].sum().reset_index()
                net_gex = net_gex.sort_values(by='strike').reset_index(drop=True)

                net_gex['GEX_Formatted'] = net_gex['GEX'].apply(format_large_number)
                
                # We now check if the breakdown is empty instead of net_gex
                if gex_breakdown.empty:
                    st.warning(f"⚠️ No strikes found within range.")
                else:
                    zero_crossings = []
                    
                    for i in range(1, len(net_gex)):
                        gex1 = net_gex['GEX'].iloc[i-1]
                        gex2 = net_gex['GEX'].iloc[i]
                        
                        # If the GEX values multiply to a negative, it means one is positive and one is negative (a sign flip)
                        if gex1 * gex2 < 0:
                            strike1 = net_gex['strike'].iloc[i-1]
                            strike2 = net_gex['strike'].iloc[i]
                            # Interpolate exact price where GEX crosses 0
                            zero_price = strike1 - gex1 * ((strike2 - strike1) / (gex2 - gex1))
                            zero_crossings.append(zero_price)
                    
                    # If multiple flips exist, isolate the one closest to the current spot price
                    gamma_flip = None
                    if zero_crossings:
                        gamma_flip = min(zero_crossings, key=lambda x: abs(x - spot_price))

                    # Format the title to show all selected dates
                        title_dates = ", ".join(raw_selected_exps)

                    # --- NEW FEATURE: Upper Right Chart Toggle ---
                    # We use columns to push the toggle switch to the right side of the screen
                    col1, col2 = st.columns([5, 1])
                    with col2:
                        show_split = st.toggle("Split Put/Call View", value=True)

                    # 4. Plotting with Plotly
                    if show_split:
                        # Render the Split Green/Red Chart
                        fig = px.bar(
                            gex_breakdown, 
                            x='strike', 
                            y='GEX', 
                            color='Type', 
                            title=f"Put/Call GEX Profile for {ticker_input} ({title_dates})",
                            labels={'strike': 'Strike Price', 'GEX': 'Gamma Exposure ($)', 'Type': 'Option Type'},
                            color_discrete_map={'Call': '#00E676', 'Put': '#FF1744'}, 
                            custom_data=['GEX_Formatted'] 
                        )
                    else:
                        # Render the Original Net GEX Heatmap Chart
                        fig = px.bar(
                            net_gex, 
                            x='strike', 
                            y='GEX', 
                            title=f"Net GEX Profile for {ticker_input} ({title_dates})",
                            labels={'strike': 'Strike Price', 'GEX': 'Net Gamma Exposure ($)'},
                            color='GEX',
                            color_continuous_scale=[(0, "red"), (0.5, "white"), (1, "green")],
                            color_continuous_midpoint=0,
                            custom_data=['GEX_Formatted'] 
                        )
                    
                    # Apply the hover template to whichever chart is active
                    fig.update_traces(hovertemplate='<b>Strike:</b> %{x}<br><b>GEX:</b> %{customdata[0]}<extra></extra>')
                    
                    # Add Spot Price Line
                    fig.add_vline(
                        x=spot_price, 
                        line_dash="dash", 
                        line_color="blue", 
                        annotation_text=f"Spot: ${spot_price:.2f}",
                        annotation_position="top" 
                    )
                    
                    # Add Gamma Flip Line
                    if gamma_flip:
                        fig.add_vline(
                            x=gamma_flip, 
                            line_dash="dot", 
                            line_color="orange", 
                            annotation_text=f"Gamma Flip: ${gamma_flip:.2f}",
                            annotation_position="bottom",
                            annotation_font_color="orange"
                        )
                    
                    startup_x_min = spot_price * 0.95
                    startup_x_max = spot_price * 1.05

                    fig.update_layout(
                        xaxis=dict(
                            tickformat='~g',
                            range=[startup_x_min, startup_x_max],
                            fixedrange=False,
                            automargin=True,
                            title_standoff=15
                        ), 
                        yaxis=dict(
                            fixedrange=False 
                        ),
                        template="plotly_dark", 
                        dragmode="zoom",         
                        height=600,                 
                        margin=dict(t=50, b=100, l=50, r=20), # <--- Forces an 80px bottom margin so labels NEVER cut off
                        font=dict(size=14),         # <--- Slightly scaled down so the text doesn't crowd the container
                        bargap=0.15,                
                        hoverlabel=dict(font_size=16),
                        legend_title_text='',
                        autosize=True               # <--- Ensures Plotly listens to Streamlit's container width
                    )
                    
                    st.plotly_chart(fig, width='stretch', config={'scrollZoom': True})

                    # --- Dynamic Raw Data Table ---
                    st.markdown("### Raw Strike Data")
                    
                    # 1. Prepare Base DataFrame based on View
                    if show_split:
                        split_table = df_filtered.pivot_table(
                            index='strike', columns='Type', values='GEX', aggfunc='sum'
                        ).reset_index().fillna(0)
                        
                        if 'Call' not in split_table.columns: split_table['Call'] = 0
                        if 'Put' not in split_table.columns: split_table['Put'] = 0
                            
                        split_table['Call GEX'] = split_table['Call'].apply(format_large_number)
                        split_table['Put GEX'] = split_table['Put'].apply(format_large_number)
                        
                        display_df = split_table[['strike', 'Call GEX', 'Put GEX']].sort_values('strike')
                        display_df.rename(columns={'strike': 'Strike'}, inplace=True)
                    else:
                        display_df = net_gex[['strike', 'GEX_Formatted']].copy()
                        display_df.rename(columns={'strike': 'Strike', 'GEX_Formatted': 'Net GEX'}, inplace=True)
                        display_df = display_df.sort_values('Strike')
                        
                    # 2. Apply Universal ITM Shading
                    def style_itm_universal(data):
                        # Create an empty styling grid that matches the table's dimensions
                        styles = pd.DataFrame('', index=data.index, columns=data.columns)
                        
                        # Failsafe: Force the Strike column to strictly evaluate as float math
                        strikes = data['Strike'].astype(float)
                        
                        # Apply shading exactly where the conditions are met
                        if 'Call GEX' in data.columns:
                            styles.loc[strikes < spot_price, 'Call GEX'] = 'background-color: rgba(128, 128, 128, 0.2)'
                            
                        if 'Put GEX' in data.columns:
                            styles.loc[strikes > spot_price, 'Put GEX'] = 'background-color: rgba(128, 128, 128, 0.2)'
                            
                        if 'Net GEX' in data.columns:
                            styles.loc[strikes > spot_price, 'Net GEX'] = 'background-color: rgba(128, 128, 128, 0.2)'
                            
                        return styles
                        
                    styled_df = display_df.style.apply(style_itm_universal, axis=None)
                    
                    # 3. Render Table with Dynamic Column Configuration
                    dynamic_col_config = {"Strike": st.column_config.NumberColumn("Strike", width="medium",format="%g")}
                    for col in display_df.columns:
                        if col != "Strike":
                            dynamic_col_config[col] = st.column_config.Column(col, width="medium")
                    
                    st.dataframe(
                        styled_df, 
                        width='stretch',
                        hide_index=True,
                        column_config=dynamic_col_config
                    )