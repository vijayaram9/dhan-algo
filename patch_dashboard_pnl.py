import re

# 1. Patch templates/base.html to include Real-Time Net P&L Badge in navbar
with open('templates/base.html', 'r', encoding='utf-8') as f:
    base_html = f.read()

target_cash = '''                <!-- Live Dhan Real Cash Balance -->
                <div class="hidden sm:flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-slate-100 border border-black/[0.04] text-xs font-mono" title="Live cash balance in your Dhan broker trading account. Reflects wallet deposits/top-ups in real-time.">
                    <span class="text-[10px] font-sans font-bold text-slate-500 uppercase tracking-tight">Dhan Cash:</span>
                    <span id="nav-dhan-cash" class="font-black text-slate-900">₹0.00</span>
                    <button onclick="syncDhanFunds(this)" title="Sync live Dhan wallet balance" class="text-slate-400 hover:text-blue-600 transition ml-0.5">
                        <i id="nav-funds-sync-icon" class="fa-solid fa-arrows-rotate text-[10px]"></i>
                    </button>
                </div>'''

pnl_pill = '''                <!-- Live Dhan Real Cash Balance -->
                <div class="hidden sm:flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-slate-100 border border-black/[0.04] text-xs font-mono" title="Live cash balance in your Dhan broker trading account. Reflects wallet deposits/top-ups in real-time.">
                    <span class="text-[10px] font-sans font-bold text-slate-500 uppercase tracking-tight">Dhan Cash:</span>
                    <span id="nav-dhan-cash" class="font-black text-slate-900">₹0.00</span>
                    <button onclick="syncDhanFunds(this)" title="Sync live Dhan wallet balance" class="text-slate-400 hover:text-blue-600 transition ml-0.5">
                        <i id="nav-funds-sync-icon" class="fa-solid fa-arrows-rotate text-[10px]"></i>
                    </button>
                </div>

                <!-- Live 1-Second Net P&L Stream (Realized + Unrealized) -->
                <div id="nav-live-net-pnl-pill" class="flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-slate-100 border border-black/[0.04] text-xs font-mono transition-colors duration-200" title="Real-time Net P&L updating second-by-second (Realized + Open Position).">
                    <div id="nav-pnl-dot" class="w-2 h-2 rounded-full bg-slate-400"></div>
                    <span class="text-[10px] font-sans font-bold text-slate-500 uppercase tracking-tight">Net P&L:</span>
                    <span id="nav-live-net-pnl-val" class="font-black text-slate-700">₹0.00</span>
                </div>'''

if target_cash in base_html and 'nav-live-net-pnl-val' not in base_html:
    base_html = base_html.replace(target_cash, pnl_pill)
    with open('templates/base.html', 'w', encoding='utf-8') as f:
        f.write(base_html)
    print("Patched base.html with live Net P&L badge!")
else:
    print("base.html already has Net P&L badge or target not found.")

# 2. Patch templates/dashboard.html
with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    dash_html = f.read()

# Fix end of dashboard.html (remove trailing code after {% endblock %} and put executeBreakevenLock inside script)
stray_end = '''</script>
{% endblock %}
        async function executeBreakevenLock() {
            try {
                const res = await fetch('/api/lock-breakeven', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showTradingToast("Breakeven Locked", data.message || "SL moved to entry + 1.0 pt.", "success");
                    pollStatus();
                } else {
                    showTradingToast("Notice", data.error || data.message, "warning");
                }
            } catch (e) {
                console.error("Lock error:", e);
            }
        }'''

fixed_end = '''        async function executeBreakevenLock() {
            try {
                const res = await fetch('/api/lock-breakeven', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showTradingToast("Breakeven Locked", data.message || "SL moved to entry + 1.0 pt.", "success");
                    fetchStatus();
                } else {
                    showTradingToast("Notice", data.error || data.message, "warning");
                }
            } catch (e) {
                console.error("Lock error:", e);
            }
        }
    });
</script>
{% endblock %}'''

# Replace the end
if 'async function executeBreakevenLock()' in dash_html and '{% endblock %}\n        async function executeBreakevenLock()' in dash_html:
    # Pattern replace
    dash_html = re.sub(
        r'\}\);\s*</script>\s*\{% endblock %\s*\}\s*async function executeBreakevenLock\(\)[\s\S]*',
        fixed_end,
        dash_html
    )
    print("Fixed dashboard.html script end structure.")

# Update the 1-second interval cadence in dashboard.html
old_interval = '''        // 1-Second Real-Time Core Engine, Sniper HUD & Ticker Stream
        setInterval(() => {
            fetchStatus(); // Fast 1-second live polling for position countdown, P&L, and trailing stream
            fetchMarketIntelligence(); // Live 1-second spot and institutional pivot updates (VWAP, PP, R1, S1)
            fetchSniperScan(); // Live 1-second high-velocity scalp candidate scanner
            if (isRadarLiveSync) {
                fetchOpportunities(); // Live 1-second multi-asset opportunity radar
            }
            orderFlowCountdown--;
            if (orderFlowCountdown <= 0) {
                orderFlowCountdown = 10;
                pollOrderFlow10s();
                fetchMarketNews();
            }
            const timerBadge = document.getElementById('of-timer-badge');
            if (timerBadge) timerBadge.innerText = `Tick in: ${orderFlowCountdown}s`;
            const newsTimerBadge = document.getElementById('news-timer-badge');
            if (newsTimerBadge) newsTimerBadge.innerText = `Tick in: ${orderFlowCountdown}s`;
        }, 1000);'''

new_interval = '''        // 1. High-Speed 1-Second Core Engine, Second-by-Second Net P&L & Scrip LTP Stream
        setInterval(() => {
            fetchStatus(); // Fast <10ms live polling: Active Position Net P&L, Second Ticker & Rules
            fetchMarketIntelligence(); // Live 1-second spot and institutional pivot updates (VWAP, PP, R1, S1)
            
            orderFlowCountdown--;
            if (orderFlowCountdown <= 0) {
                orderFlowCountdown = 10;
                pollOrderFlow10s();
                fetchMarketNews();
            }
            const timerBadge = document.getElementById('of-timer-badge');
            if (timerBadge) timerBadge.innerText = `Tick in: ${orderFlowCountdown}s`;
            const newsTimerBadge = document.getElementById('news-timer-badge');
            if (newsTimerBadge) newsTimerBadge.innerText = `Tick in: ${orderFlowCountdown}s`;
        }, 1000);

        // 2. High-Velocity Sniper Scanner & Multi-Asset Opportunity Radar (2.5s Cadence)
        setInterval(() => {
            fetchSniperScan(); // In-memory cached high-velocity candidate scan
            if (isRadarLiveSync) {
                fetchOpportunities(); // Multi-asset opportunity radar
            }
        }, 2500);'''

if old_interval in dash_html:
    dash_html = dash_html.replace(old_interval, new_interval)
    print("Updated dashboard.html interval cadence.")

with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write(dash_html)
print("Saved dashboard.html!")
