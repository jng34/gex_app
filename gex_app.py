from scipy.stats import norm
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.express as px
import datetime

# --- CSS Hack: Hide Crosshair, Keep Axis Stretch Arrows ---
st.markdown(
    """
    <style>
    /* This forces the main chart cursor to be a normal arrow, 
       while allowing the X and Y axes to keep their sideways/up-down stretch arrows! */
    .js-plotly-plot .plotly .cursor-crosshair {
        cursor: default !important;
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
            return None, []
            
        spot_price = float(hist_reg['Close'].iloc[-1])
        
        # If extended hours exist, grab the latest tick. Otherwise, default to spot.
        ext_price = float(hist_ext['Close'].iloc[-1]) if not hist_ext.empty else spot_price

        exps = list(temp_ticker.options)
        return spot_price, ext_price, exps
    except Exception as e:
        return None, []

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

        # --- NEW FEATURE: Multi-Select with a max of 4 ---
        selected_exps = st.sidebar.multiselect(
            "Select Expiration Date(s)", 
            expirations, 
            default=[expirations[0]] if expirations else None,
            max_selections=4
        )

        # 3. Process Options Chains
        if selected_exps:
            all_dfs = [] # List to hold dataframes from multiple expirations
            
            # Loop through each selected expiration
            for exp in selected_exps:
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

                net_gex = df_filtered.groupby('strike')['GEX'].sum().reset_index()
                
                if net_gex.empty:
                    st.warning("⚠️ Data pulled successfully, but no strikes found within 15% of the current spot price with active Open Interest.")
                else:
                    # --- NEW FEATURE: Apply the formatting function ---
                    net_gex['GEX_Formatted'] = net_gex['GEX'].apply(format_large_number)

                    # --- NEW FEATURE: Calculate Gamma Flip (Zero Gamma) ---
                    # Sort strikes chronologically to map the transitions properly
                    net_gex = net_gex.sort_values(by='strike').reset_index(drop=True)
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
                    title_dates = ", ".join(selected_exps)

                    # 4. Plotting with Plotly
                    fig = px.bar(
                        net_gex, 
                        x='strike', 
                        y='GEX', 
                        title=f"Net GEX for {ticker_input} ({title_dates})",
                        labels={'strike': 'Strike Price', 'GEX': 'Net Gamma Exposure ($)'},
                        color='GEX',
                        color_continuous_scale=[(0, "red"), (0.5, "white"), (1, "green")],
                        color_continuous_midpoint=0,
                        custom_data=['GEX_Formatted']
                    )

                    fig.update_layout(
                        xaxis=dict(
                            title=dict(text='Strike Price', font=dict(size=18)),
                            tickfont=dict(size=16)
                        ),
                        yaxis=dict(
                            title=dict(text='Net Gamma Exposure ($)', font=dict(size=18)),
                            tickfont=dict(size=16)
                        ),
                        font=dict(size=16)  # general text size for title / legend
                    )
                                        
                    fig.update_traces(hovertemplate='<b>Strike:</b> %{x}<br><b>Net GEX:</b> %{customdata[0]}<extra></extra>')
                    
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
                    
                    # Define startup zoom bounds
                    startup_x_min = spot_price * 0.95
                    startup_x_max = spot_price * 1.05

                    fig.update_layout(
                        xaxis=dict(
                            tickformat='d',
                            range=[startup_x_min, startup_x_max],
                            fixedrange=False # Explicitly unlocks the X-axis for stretching
                        ), 
                        yaxis=dict(
                            fixedrange=False # Explicitly unlocks the Y-axis for stretching
                        ),
                        template="plotly_dark", 
                        dragmode="zoom",          
                        height=750,                 
                        font=dict(size=16),         
                        bargap=0.15,                
                        hoverlabel=dict(font_size=18) 
                    )

                    st.plotly_chart(fig, width='stretch', config={'scrollZoom': True})

                    st.write("### Raw Strike Data")
                    st.dataframe(net_gex.sort_values(by='strike'))