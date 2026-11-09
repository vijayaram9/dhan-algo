with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Update Hero Card in HTML to include Lot Counter
old_hero_btns = '''                    <div class="flex items-center gap-2">
                        <button id="btn-fire-top-pick" onclick="triggerBestSniperPick()"
                            class="px-5 py-3 rounded-xl bg-gradient-to-r from-amber-500 via-emerald-600 to-teal-600 hover:from-amber-600 hover:to-teal-700 text-white font-black text-xs font-mono shadow-md hover:shadow-lg transition flex items-center space-x-2">
                            <i class="fa-solid fa-crosshairs text-amber-200 text-sm"></i>
                            <span id="btn-fire-top-label">⚡ Fire #1 Top Sniper Scalp (1-Click)</span>
                        </button>
                    </div>'''

new_hero_btns = '''                    <div class="flex flex-wrap items-center gap-2">
                        <!-- Interactive Top Pick Lot Customizer -->
                        <div class="flex items-center bg-white p-1 rounded-xl border border-slate-200 text-slate-800 font-mono shadow-2xs">
                            <button type="button" onclick="adjustTopPickLots(-1)" class="w-8 h-8 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-black text-sm flex items-center justify-center cursor-pointer transition active:scale-95" title="Decrease Lots">-</button>
                            <span id="top-pick-lots-display" class="px-3 font-black text-xs text-slate-900">1 Lot</span>
                            <button type="button" onclick="adjustTopPickLots(+1)" class="w-8 h-8 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-black text-sm flex items-center justify-center cursor-pointer transition active:scale-95" title="Increase Lots">+</button>
                        </div>

                        <button id="btn-fire-top-pick" onclick="triggerBestSniperPick()"
                            class="px-5 py-3 rounded-xl bg-gradient-to-r from-amber-500 via-emerald-600 to-teal-600 hover:from-amber-600 hover:to-teal-700 text-white font-black text-xs font-mono shadow-md hover:shadow-lg transition flex items-center space-x-2 active:scale-95 cursor-pointer">
                            <i class="fa-solid fa-crosshairs text-amber-200 text-sm"></i>
                            <span id="btn-fire-top-label">⚡ Fire #1 Top Sniper Scalp (1-Click)</span>
                        </button>
                    </div>'''

if old_hero_btns in html:
    html = html.replace(old_hero_btns, new_hero_btns, 1)
    print("Updated Hero card with Lot Selector!")
else:
    print("Could not find old_hero_btns in html")

# 2. Update fetchSniperScan candidate rendering and trigger functions
old_grid_render = '''                // Render Candidates Grid
                const grid = document.getElementById('sniper-candidates-grid');
                if (grid && data.candidates) {
                    if (data.candidates.length === 0) {
                        grid.innerHTML = `<div class="col-span-full p-3 rounded-xl bg-slate-50 border border-slate-200 text-center text-xs text-slate-500 font-mono">No qualified setups fitting current capital right now.</div>`;
                    } else {
                        grid.innerHTML = data.candidates.slice(0, 8).map(c => {
                            const isCE = c.action === 'BUY_CE';
                            const badgeBg = isCE ? 'bg-emerald-50 text-emerald-800 border-emerald-200' : 'bg-rose-50 text-rose-800 border-rose-200';
                            return `
                                <div class="p-2.5 rounded-xl border border-slate-200/90 bg-white hover:border-slate-300 transition flex flex-col justify-between shadow-2xs">
                                    <div>
                                        <div class="flex justify-between items-start mb-1">
                                            <span class="font-black text-xs text-slate-900 font-mono">${c.display_name}</span>
                                            <span class="text-[9px] px-1.5 py-0.2 rounded font-black font-mono border ${badgeBg}">${c.option_type}</span>
                                        </div>
                                        <div class="text-[10px] text-slate-500 font-mono">
                                            LTP: <strong>₹${c.premium.toFixed(2)}</strong> &bull; Margin: <strong>₹${c.margin_required.toLocaleString('en-IN')}</strong>
                                        </div>
                                        <div class="text-[9px] text-slate-400 font-mono mt-0.5">
                                            Target: +₹${c.potential_gain_inr.toFixed(0)} | Risk: -₹${c.max_risk_inr.toFixed(0)}
                                        </div>
                                    </div>
                                    <button onclick="triggerSniperCandidate('${c.asset}', '${c.action}')"
                                        class="mt-2 w-full py-1.5 px-2 rounded-lg ${isCE ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-rose-600 hover:bg-rose-700'} text-white font-bold text-[10px] font-mono transition flex items-center justify-center space-x-1 shadow-2xs">
                                        <i class="fa-solid fa-crosshairs text-[10px]"></i>
                                        <span>⚡ Scalp 1 Lot</span>
                                    </button>
                                </div>
                            `;
                        }).join('');
                    }
                }'''

new_grid_render = '''                // Render Candidates Grid with Interactive Lot Customizers
                const grid = document.getElementById('sniper-candidates-grid');
                if (grid && data.candidates) {
                    if (data.candidates.length === 0) {
                        grid.innerHTML = `<div class="col-span-full p-3 rounded-xl bg-slate-50 border border-slate-200 text-center text-xs text-slate-500 font-mono">No qualified setups fitting current capital right now.</div>`;
                    } else {
                        grid.innerHTML = data.candidates.slice(0, 8).map(c => {
                            const isCE = c.action === 'BUY_CE';
                            const badgeBg = isCE ? 'bg-emerald-50 text-emerald-800 border-emerald-200' : 'bg-rose-50 text-rose-800 border-rose-200';
                            const key = `${c.asset}_${c.action}`;
                            const currentLots = (window.__sniperCardLots && window.__sniperCardLots[key]) ? window.__sniperCardLots[key] : 1;
                            const totalMargin = c.margin_required * currentLots;

                            return `
                                <div class="p-3 rounded-xl border border-slate-200/90 bg-white hover:border-slate-300 transition flex flex-col justify-between shadow-2xs">
                                    <div>
                                        <div class="flex justify-between items-start mb-1">
                                            <span class="font-black text-xs text-slate-900 font-mono">${c.display_name}</span>
                                            <span class="text-[9px] px-1.5 py-0.2 rounded font-black font-mono border ${badgeBg}">${c.option_type}</span>
                                        </div>
                                        <div class="text-[10px] text-slate-500 font-mono">
                                            LTP: <strong>₹${c.premium.toFixed(2)}</strong> &bull; Lot: <strong>${c.lot_size} Qty</strong>
                                        </div>
                                        <div class="text-[9px] text-slate-400 font-mono mt-0.5">
                                            Target: +₹${(c.potential_gain_inr * currentLots).toFixed(0)} | Risk: -₹${(c.max_risk_inr * currentLots).toFixed(0)}
                                        </div>
                                    </div>

                                    <!-- Candidate Lot Sizing Controller -->
                                    <div class="mt-2.5 pt-2 border-t border-slate-100 flex items-center justify-between">
                                        <div class="flex items-center space-x-1 bg-slate-100 rounded-lg p-0.5 border border-slate-200/60">
                                            <button type="button" onclick="adjustCandidateLot('${c.asset}', '${c.action}', -1, ${c.premium}, ${c.lot_size})" 
                                                class="w-6 h-6 rounded bg-white hover:bg-slate-200 text-slate-800 font-black text-xs flex items-center justify-center cursor-pointer transition shadow-2xs active:scale-90">-</button>
                                            <span id="cand-lots-${c.asset}-${c.action}" class="w-12 text-center font-black font-mono text-[11px] text-slate-900">${currentLots} Lot${currentLots > 1 ? 's' : ''}</span>
                                            <button type="button" onclick="adjustCandidateLot('${c.asset}', '${c.action}', +1, ${c.premium}, ${c.lot_size})" 
                                                class="w-6 h-6 rounded bg-white hover:bg-slate-200 text-slate-800 font-black text-xs flex items-center justify-center cursor-pointer transition shadow-2xs active:scale-90">+</button>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-[9px] text-slate-400 block font-sans font-semibold">Total Margin</span>
                                            <strong id="cand-margin-${c.asset}-${c.action}" class="text-[10px] text-slate-900 font-mono font-black">₹${totalMargin.toLocaleString('en-IN', {minimumFractionDigits: 1, maximumFractionDigits: 1})}</strong>
                                        </div>
                                    </div>

                                    <button id="cand-btn-${c.asset}-${c.action}" onclick="triggerSniperCandidate('${c.asset}', '${c.action}')"
                                        class="mt-2 w-full py-2 px-2 rounded-xl ${isCE ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-rose-600 hover:bg-rose-700'} text-white font-black text-[11px] font-mono transition flex items-center justify-center space-x-1.5 shadow-sm active:scale-95 cursor-pointer">
                                        <i class="fa-solid fa-crosshairs text-[10px]"></i>
                                        <span id="cand-btn-txt-${c.asset}-${c.action}">⚡ Scalp ${currentLots} Lot${currentLots > 1 ? 's' : ''}</span>
                                    </button>
                                </div>
                            `;
                        }).join('');
                    }
                }'''

if old_grid_render in html:
    html = html.replace(old_grid_render, new_grid_render, 1)
    print("Updated Grid candidate rendering with Lot Controllers!")
else:
    print("Could not find old_grid_render")

# 3. Add adjustTopPickLots, adjustCandidateLot, and upgraded trigger functions
old_triggers = '''    async function triggerBestSniperPick() {
        if (!currentSniperTopPick) {
            showTradingToast("Scanning Market", "Finding the #1 highest-confluence scalp opportunity...", "info");
            await fetchSniperScan(true);
        }
        if (!currentSniperTopPick) {
            showTradingToast("No Setup", "Scanning completed. No qualified setups fitting active capital right now.", "warning");
            return;
        }
        const p = currentSniperTopPick;
        const totalMargin = p.margin_required * activeLots;

        showTradingToast("Firing #1 Top Scalp", `Dispatching ${p.symbol} (${p.direction}) • Margin: ₹${totalMargin.toLocaleString('en-IN')}`, "info");

        try {
            const res = await fetch('/api/trigger-best-sniper', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ asset: p.asset, action: p.action, mode: currentExecutionMode, lots: activeLots })
            });
            const data = await res.json();
            if (data.success) {
                showTradingToast("⚡ Scalp Fired Successfully!", data.message || `Dispatched ${p.symbol}!`, "success");
                try { ringTempleBell(); } catch(e) {}
                fetchStatus();
                fetchMarketIntelligence();
            } else {
                showTradingToast("Execution Notice", data.error || 'Failed to dispatch scalp.', "warning");
            }
        } catch(e) {
            showTradingToast("Trigger Failed", e.message || String(e), "error");
        }
    }

    async function triggerSniperCandidate(asset, action) {
        showTradingToast("Executing Scalp", `Dispatching ${asset} (${action}) in ${currentExecutionMode} mode...`, "info");
        try {
            const res = await fetch('/api/trigger-best-sniper', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ asset: asset, action: action, mode: currentExecutionMode, lots: activeLots })
            });
            const data = await res.json();
            if (data.success) {
                showTradingToast("⚡ Scalp Dispatched!", data.message || `Executed ${asset} ${action}!`, "success");
                try { ringTempleBell(); } catch(e) {}
                fetchStatus();
                fetchMarketIntelligence();
            } else {
                showTradingToast("Execution Notice", data.error || 'Failed to dispatch.', "warning");
            }
        } catch(e) {
            showTradingToast("Execution Failed", e.message || String(e), "error");
        }
    }'''

new_triggers = '''    let sniperTopLots = 1;
    window.__sniperCardLots = {};

    function adjustTopPickLots(delta) {
        if (!currentSniperTopPick) return;
        const p = currentSniperTopPick;
        const maxAllowed = activeCapital * 0.50;
        const maxLots = Math.max(1, Math.floor(maxAllowed / (p.margin_required || 1)));
        
        let target = sniperTopLots + delta;
        if (target > maxLots) {
            alert(`🛑 50% Capital Safety Ceiling:\\n\\nMaximum permissible lots for ${p.display_name} is ${maxLots} Lot(s) (₹${(maxLots * p.margin_required).toLocaleString('en-IN')}) based on your 50% capital safety rule (₹${maxAllowed.toLocaleString('en-IN')}).`);
            target = maxLots;
        }
        sniperTopLots = Math.max(1, target);
        
        const display = document.getElementById('top-pick-lots-display');
        if (display) display.innerText = `${sniperTopLots} Lot${sniperTopLots > 1 ? 's' : ''}`;

        const btnLabel = document.getElementById('btn-fire-top-label');
        if (btnLabel) {
            const totalMargin = (p.margin_required * sniperTopLots).toLocaleString('en-IN');
            btnLabel.innerText = `⚡ Fire ${p.asset} ${p.option_type} (${sniperTopLots} Lot${sniperTopLots > 1 ? 's' : ''} • Margin: ₹${totalMargin})`;
        }
    }

    function adjustCandidateLot(asset, action, delta, premium, lotSize) {
        const key = `${asset}_${action}`;
        const currentLots = (window.__sniperCardLots && window.__sniperCardLots[key]) ? window.__sniperCardLots[key] : 1;
        const marginPerLot = premium * lotSize;
        const maxAllowed = activeCapital * 0.50;
        const maxLots = Math.max(1, Math.floor(maxAllowed / (marginPerLot || 1)));

        let target = currentLots + delta;
        if (target > maxLots) {
            alert(`🛑 50% Capital Safety Ceiling:\\n\\nMaximum permissible lots for ${asset} is ${maxLots} Lot(s) (₹${(maxLots * marginPerLot).toLocaleString('en-IN')}) based on your 50% capital safety rule (₹${maxAllowed.toLocaleString('en-IN')}).`);
            target = maxLots;
        }
        const finalLots = Math.max(1, target);
        window.__sniperCardLots[key] = finalLots;

        const lotsEl = document.getElementById(`cand-lots-${asset}-${action}`);
        if (lotsEl) lotsEl.innerText = `${finalLots} Lot${finalLots > 1 ? 's' : ''}`;

        const marginEl = document.getElementById(`cand-margin-${asset}-${action}`);
        if (marginEl) marginEl.innerText = `₹${(finalLots * marginPerLot).toLocaleString('en-IN', {minimumFractionDigits: 1, maximumFractionDigits: 1})}`;

        const btnTxt = document.getElementById(`cand-btn-txt-${asset}-${action}`);
        if (btnTxt) btnTxt.innerText = `⚡ Scalp ${finalLots} Lot${finalLots > 1 ? 's' : ''}`;
    }

    async function triggerBestSniperPick(replace = false) {
        if (!currentSniperTopPick) {
            showTradingToast("Scanning Market", "Finding the #1 highest-confluence scalp opportunity...", "info");
            await fetchSniperScan(true);
        }
        if (!currentSniperTopPick) {
            showTradingToast("No Setup", "Scanning completed. No qualified setups fitting active capital right now.", "warning");
            return;
        }
        const p = currentSniperTopPick;
        const totalMargin = p.margin_required * sniperTopLots;

        showTradingToast("Firing #1 Top Scalp", `Dispatching ${p.symbol} (${sniperTopLots} Lots) • Margin: ₹${totalMargin.toLocaleString('en-IN')}`, "info");

        try {
            const res = await fetch('/api/trigger-best-sniper', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ asset: p.asset, action: p.action, mode: currentExecutionMode, lots: sniperTopLots, replace: replace })
            });
            const data = await res.json();
            if (data.success) {
                showTradingToast("⚡ Scalp Fired Successfully!", data.message || `Dispatched ${p.symbol}!`, "success");
                try { ringTempleBell(); } catch(e) {}
                fetchStatus();
                fetchMarketIntelligence();
            } else if (data.has_active_position) {
                if (confirm(`⚠️ Position Already Active In-Flight!\\n\\nYou currently have an open position in: ${data.active_symbol}.\\n\\nWould you like to Square-Off that position and immediately enter this new ${sniperTopLots} Lot(s) scalp on ${p.symbol}?`)) {
                    triggerBestSniperPick(true);
                }
            } else {
                showTradingToast("Execution Notice", data.error || 'Failed to dispatch scalp.', "warning");
            }
        } catch(e) {
            showTradingToast("Trigger Failed", e.message || String(e), "error");
        }
    }

    async function triggerSniperCandidate(asset, action, replace = false) {
        const key = `${asset}_${action}`;
        const lots = (window.__sniperCardLots && window.__sniperCardLots[key]) ? window.__sniperCardLots[key] : 1;

        showTradingToast("Executing Scalp", `Dispatching ${asset} ${action} (${lots} Lot${lots > 1 ? 's' : ''}) in ${currentExecutionMode} mode...`, "info");
        try {
            const res = await fetch('/api/trigger-best-sniper', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ asset: asset, action: action, mode: currentExecutionMode, lots: lots, replace: replace })
            });
            const data = await res.json();
            if (data.success) {
                showTradingToast("⚡ Scalp Dispatched!", data.message || `Executed ${asset} ${action}!`, "success");
                try { ringTempleBell(); } catch(e) {}
                fetchStatus();
                fetchMarketIntelligence();
            } else if (data.has_active_position) {
                if (confirm(`⚠️ Position Already Active In-Flight!\\n\\nYou currently have an open position in: ${data.active_symbol}.\\n\\nWould you like to Square-Off that position and immediately enter this new ${lots} Lot(s) scalp on ${asset}?`)) {
                    triggerSniperCandidate(asset, action, true);
                }
            } else {
                showTradingToast("Execution Notice", data.error || 'Failed to dispatch.', "warning");
            }
        } catch(e) {
            showTradingToast("Execution Failed", e.message || String(e), "error");
        }
    }'''

if old_triggers in html:
    html = html.replace(old_triggers, new_triggers, 1)
    print("Replaced triggers with multi-lot support and replace confirmation!")
else:
    print("Could not find old_triggers in html")

with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("Saved templates/dashboard.html successfully!")
