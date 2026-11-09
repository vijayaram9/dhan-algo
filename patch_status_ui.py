with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Add nav PnL updater inside fetchStatus()
target_fetch = '''    async function fetchStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();

            // Update Real-Time Sniper HUD & Scrolling Event Terminal'''

new_fetch = '''    async function fetchStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();

            // 0. Update Real-Time Net P&L in Navbar every second (Realized + Open Position)
            const navPnlVal = document.getElementById('nav-live-net-pnl-val');
            const navPnlDot = document.getElementById('nav-pnl-dot');
            const navPnlPill = document.getElementById('nav-live-net-pnl-pill');
            if (navPnlVal && data.rms) {
                const realizedPnl = data.rms.net_pnl || 0.0;
                const openPnl = (data.active_position && typeof data.active_position.pnl === 'number') ? data.active_position.pnl : 0.0;
                const totalNetPnl = realizedPnl + openPnl;
                const sign = totalNetPnl >= 0 ? '+' : '';
                navPnlVal.innerText = `${sign}₹${totalNetPnl.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

                if (totalNetPnl > 0) {
                    navPnlVal.className = "font-black text-emerald-700";
                    if (navPnlDot) navPnlDot.className = "w-2 h-2 rounded-full bg-emerald-500 animate-pulse";
                    if (navPnlPill) navPnlPill.className = "flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-emerald-50 border border-emerald-200 text-xs font-mono transition-colors duration-200";
                } else if (totalNetPnl < 0) {
                    navPnlVal.className = "font-black text-rose-700";
                    if (navPnlDot) navPnlDot.className = "w-2 h-2 rounded-full bg-rose-500 animate-pulse";
                    if (navPnlPill) navPnlPill.className = "flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-rose-50 border border-rose-200 text-xs font-mono transition-colors duration-200";
                } else {
                    navPnlVal.className = "font-black text-slate-700";
                    if (navPnlDot) navPnlDot.className = "w-2 h-2 rounded-full bg-slate-400";
                    if (navPnlPill) navPnlPill.className = "flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-slate-100 border border-black/[0.04] text-xs font-mono transition-colors duration-200";
                }
            }

            // Update Real-Time Sniper HUD & Scrolling Event Terminal'''

if target_fetch in content:
    content = content.replace(target_fetch, new_fetch, 1)
    print("Added nav P&L updater in fetchStatus!")
else:
    print("Could not find target_fetch")

# Upgrade #active-position-card initial HTML
target_card = '''        <!-- Live Active Position Card Display (Shows full tracking when position is open) -->
        <div id="active-position-card" class="mb-3">
            <div class="p-4 rounded-xl bg-slate-50 border border-black/[0.03] text-center text-xs text-slate-500">
                <i class="fa-solid fa-shield-cat text-xl text-slate-400 mb-1 block"></i>
                No open positions right now. Bot is actively scanning and will automatically enter when confirmation rules align.
            </div>
        </div>'''

new_card = '''        <!-- Live Active Position Card Display (Shows full tracking when position is open) -->
        <div id="active-position-card" class="mb-3">
            <div class="p-4 rounded-2xl bg-white border border-slate-200/90 shadow-2xs">
                <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 pb-2.5 mb-2.5 border-b border-slate-100">
                    <div class="flex items-center space-x-2">
                        <span class="p-1.5 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-200 text-xs font-bold">
                            <i class="fa-solid fa-shield-halved"></i>
                        </span>
                        <strong class="font-black text-xs text-slate-900 uppercase font-mono tracking-wider">CAPITAL 100% PRESERVED IN CASH</strong>
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold font-mono bg-emerald-100 text-emerald-800">ZERO OPEN RISK</span>
                    </div>
                    <span class="text-xs text-slate-500 font-mono">Session Mode: <strong class="text-slate-800">{{ config.trading_mode }}</strong></span>
                </div>

                <div class="p-3 rounded-xl bg-slate-50 border border-slate-200 text-xs text-slate-700 leading-relaxed space-y-1.5">
                    <div class="flex items-center space-x-1.5 font-bold text-slate-900">
                        <i class="fa-solid fa-circle-info text-blue-600"></i>
                        <span>Why Sitting in Cash Right Now (Discipline Engine):</span>
                    </div>
                    <p class="text-slate-700 text-[11px] leading-relaxed">
                        Evaluating multi-timeframe candle closes and order flow. Capital is 100% protected in cash until 7 of 8 confirmation rules align to eliminate whip-saw losses.
                    </p>
                </div>

                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2.5 text-xs">
                    <div class="p-2.5 rounded-xl bg-emerald-50/50 border border-emerald-200">
                        <span class="text-[10px] font-bold text-emerald-800 uppercase font-mono block">🟢 To Trigger CALL (CE):</span>
                        <span class="text-[11px] text-slate-700 font-sans mt-0.5 block">5m candle close above VWAP + EMA 9 > 21 + positive CVD delta (> +50k)</span>
                    </div>
                    <div class="p-2.5 rounded-xl bg-rose-50/50 border border-rose-200">
                        <span class="text-[10px] font-bold text-rose-800 uppercase font-mono block">🔴 To Trigger PUT (PE):</span>
                        <span class="text-[11px] text-slate-700 font-sans mt-0.5 block">Rejection breakdown below S1 floor + EMA 9 &lt; 21 + negative CVD delta (&lt; -50k)</span>
                    </div>
                </div>
            </div>
        </div>'''

if target_card in content:
    content = content.replace(target_card, new_card, 1)
    print("Upgraded initial #active-position-card HTML!")
else:
    print("Could not find target_card")

with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated templates/dashboard.html successfully!")
