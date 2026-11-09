import os
import json
import subprocess
import requests
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DEVICES_CONFIG_FILE = os.path.join(DATA_DIR, "notification_devices.json")


class TradeNotifier:
    """
    Enterprise Multi-Mobile Notification Engine:
    1. ntfy.sh Multi-Mobile Broadcast: Instant zero-delay push to 1 or 50+ iPhones / Androids in parallel.
       - Any mobile can scan the primary QR code to subscribe in 2 seconds.
       - Individual mobile devices can also register their own unique topic/channel.
    2. Windows Desktop Toast & Audio Chime (instant PC feedback).
    3. WhatsApp (CallMeBot gateway fallback).
    4. Telegram Bot (fallback).
    """

    def __init__(self):
        # Primary ntfy topic from environment (or default)
        self.primary_topic = os.getenv("NTFY_TOPIC", "sai-nifty-trades-9422").strip() or "sai-nifty-trades-9422"
        
        # WhatsApp & Telegram credentials
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.whatsapp_phone = os.getenv("WHATSAPP_PHONE", "").strip()
        self.whatsapp_apikey = os.getenv("WHATSAPP_APIKEY", "").strip()

        # Thread pool for non-blocking multi-device parallel broadcasts
        self._executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="NotifierBroadcast")

        # Load registered mobile devices
        self.devices: List[Dict[str, Any]] = self._load_devices()

    def _load_devices(self) -> List[Dict[str, Any]]:
        """Loads registered mobile device channels from JSON file."""
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(DEVICES_CONFIG_FILE):
            try:
                with open(DEVICES_CONFIG_FILE, "r", encoding="utf-8") as f:
                    devices = json.load(f)
                    if isinstance(devices, list) and len(devices) > 0:
                        # Ensure primary device is always present
                        has_primary = any(d.get("topic") == self.primary_topic for d in devices)
                        if not has_primary:
                            devices.insert(0, {
                                "id": "primary",
                                "name": "Primary Trading Phone (Master)",
                                "topic": self.primary_topic,
                                "is_primary": True,
                                "subscribe_url": f"https://ntfy.sh/{self.primary_topic}"
                            })
                        return devices
            except Exception as e:
                print(f"[Notifier] Error loading devices config: {e}")

        # Default initial device list
        default_devices = [
            {
                "id": "primary",
                "name": "Primary Trading Mobile (Master)",
                "topic": self.primary_topic,
                "is_primary": True,
                "subscribe_url": f"https://ntfy.sh/{self.primary_topic}"
            }
        ]
        self._save_devices(default_devices)
        return default_devices

    def _save_devices(self, devices: List[Dict[str, Any]]) -> None:
        """Saves registered mobile device channels to JSON file."""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(DEVICES_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(devices, f, indent=2)
        except Exception as e:
            print(f"[Notifier] Error saving devices config: {e}")

    def get_devices(self) -> List[Dict[str, Any]]:
        """Returns all registered mobile devices and subscription links."""
        return self.devices

    def add_device(self, name: str, topic: Optional[str] = None) -> Dict[str, Any]:
        """Registers a new mobile device for push alerts."""
        clean_name = (name or "Secondary Mobile").strip()
        clean_topic = (topic or "").strip()

        # If no custom topic specified, generate or use clean format
        if not clean_topic:
            slug = clean_name.lower().replace(" ", "-").replace("'", "")
            clean_topic = f"{self.primary_topic}-{slug}"

        # Clean topic characters for URL safety
        clean_topic = "".join(c for c in clean_topic if c.isalnum() or c in "-_")

        # Check for duplicates
        for d in self.devices:
            if d.get("topic") == clean_topic:
                return {
                    "success": False,
                    "error": f"Device with topic '{clean_topic}' is already registered."
                }

        device_id = f"device-{len(self.devices) + 1}-{clean_topic[:8]}"
        new_device = {
            "id": device_id,
            "name": clean_name,
            "topic": clean_topic,
            "is_primary": False,
            "subscribe_url": f"https://ntfy.sh/{clean_topic}"
        }
        self.devices.append(new_device)
        self._save_devices(self.devices)
        return {"success": True, "device": new_device}

    def remove_device(self, device_id: str) -> Dict[str, Any]:
        """Removes a registered mobile device."""
        for i, d in enumerate(self.devices):
            if d.get("id") == device_id or d.get("topic") == device_id:
                if d.get("is_primary"):
                    return {"success": False, "error": "Cannot remove the primary master device channel."}
                removed = self.devices.pop(i)
                self._save_devices(self.devices)
                return {"success": True, "removed": removed}
        return {"success": False, "error": "Device not found."}

    def _send_single_ntfy(self, topic: str, title: str, message: str, tags: str = "chart_with_upwards_trend", priority: str = "default") -> bool:
        """Sends an individual push notification to a specific ntfy.sh topic."""
        if not topic:
            return False
        try:
            url = f"https://ntfy.sh/{topic}"
            headers = {
                "Title": title.encode("utf-8"),
                "Priority": priority,
                "Tags": tags
            }
            res = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=4)
            return res.status_code == 200
        except Exception as e:
            print(f"[Notifier] Push to '{topic}' failed: {e}")
            return False

    def send_ntfy(self, title: str, message: str, tags: str = "chart_with_upwards_trend", priority: str = "default") -> bool:
        """
        Broadcasts push notification to ALL registered mobile phones concurrently.
        Every device receives the alert in parallel in < 500ms.
        """
        topics = list(set([d.get("topic") for d in self.devices if d.get("topic")]))
        if not topics:
            topics = [self.primary_topic]

        # Use thread pool to broadcast in parallel without slowing down trading execution
        try:
            futures = [
                self._executor.submit(self._send_single_ntfy, t, title, message, tags, priority)
                for t in topics
            ]
            # Don't wait excessively for completion if called during execution
            return True
        except Exception as e:
            print(f"[Notifier] Broadcast executor error: {e}")
            # Fallback direct call to primary topic
            return self._send_single_ntfy(self.primary_topic, title, message, tags, priority)

    def send_whatsapp(self, message: str) -> bool:
        """Sends WhatsApp alert via CallMeBot gateway if configured."""
        if not self.whatsapp_phone or not self.whatsapp_apikey:
            return False
        try:
            url = f"https://api.callmebot.com/whatsapp.php?phone={self.whatsapp_phone}&text={requests.utils.quote(message)}&apikey={self.whatsapp_apikey}"
            res = requests.get(url, timeout=5)
            return res.status_code == 200
        except Exception as e:
            print(f"[Notifier] WhatsApp failed: {e}")
            return False

    def send_telegram(self, message: str) -> bool:
        """Sends Telegram alert via Bot API if configured."""
        if not self.bot_token or not self.chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
            res = requests.post(url, json=payload, timeout=5)
            return res.status_code == 200
        except Exception as e:
            print(f"[Notifier] Telegram failed: {e}")
            return False

    def play_sound_and_toast(self, title: str, message: str):
        """Native Windows Desktop Notification and Chime."""
        try:
            clean_msg = message.replace("'", "").replace('"', '').replace("\n", " ")
            clean_title = title.replace("'", "").replace('"', '')
            ps_cmd = (
                f"[System.Media.SystemSounds]::Exclamation.Play(); "
                f"Add-Type -AssemblyName System.Windows.Forms; "
                f"$notify = New-Object System.Windows.Forms.NotifyIcon; "
                f"$notify.Icon = [System.Drawing.SystemIcons]::Information; "
                f"$notify.Visible = $true; "
                f"$notify.ShowBalloonTip(4000, '{clean_title}', '{clean_msg}', [System.Windows.Forms.ToolTipIcon]::Info)"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def notify(self, title: str, message: str, tags: str = "bell", priority: str = "default"):
        """Master notification: Dispatches to PC and broadcasts to all connected mobiles."""
        # 1. Desktop Notification & Audio Chime
        self.play_sound_and_toast(title, message)

        # 2. Broadcast to all mobile phones
        self.send_ntfy(title, message, tags=tags, priority=priority)

        # 3. WhatsApp and Telegram fallbacks (if configured)
        if self.whatsapp_phone and self.whatsapp_apikey:
            self._executor.submit(self.send_whatsapp, f"*{title}*\n\n{message}")
        if self.bot_token and self.chat_id:
            self._executor.submit(self.send_telegram, f"*{title}*\n\n{message}")

    def notify_trade_entry(self, symbol: str, action: str, qty: int, entry_price: float, sl: float, target: float, spot: float):
        title = f"🚀 Dhan Algo: {action} Triggered"
        body = (
            f"Contract: {symbol}\n"
            f"Qty: {qty} ({qty // 75} Lots)\n"
            f"Entry: Rs.{entry_price:.2f} | Spot: Rs.{spot:.2f}\n"
            f"SL: Rs.{sl:.2f} (-10%) | Target: Rs.{target:.2f} (+20%)"
        )
        self.notify(title, body, tags="chart_with_upwards_trend,moneybag", priority="high")

    def notify_trade_exit(self, symbol: str, reason: str, exit_price: float, entry_price: float, qty: int, pnl: float, net_pnl: float):
        status = "PROFIT" if pnl >= 0 else "LOSS"
        icon = "green_circle" if pnl >= 0 else "red_circle"
        title = f"🏁 Trade Closed: {status} (Rs.{pnl:+.2f})"
        body = (
            f"Contract: {symbol}\n"
            f"Reason: {reason}\n"
            f"Entry: Rs.{entry_price:.2f} -> Exit: Rs.{exit_price:.2f}\n"
            f"Net Daily P&L: Rs.{net_pnl:+.2f}"
        )
        self.notify(title, body, tags=f"{icon},bank", priority="high")

    def notify_kill_switch(self, reason: str, net_pnl: float):
        title = "🛑 RMS Kill Switch Activated"
        body = f"Reason: {reason}\nNet Daily P&L: Rs.{net_pnl:+.2f}\nBot halted for the day."
        self.notify(title, body, tags="warning,octagonal_sign", priority="urgent")

    def test_broadcast(self, custom_title: Optional[str] = None, custom_message: Optional[str] = None) -> Dict[str, Any]:
        """Tests sending a live push notification to each registered mobile device."""
        title = custom_title or "⚡ Vijay Sai's Dhan Algo Alert"
        message = custom_message or "Multi-Mobile Test Alert: All paired devices verified & receiving live trade signals!"
        results = {}

        for device in self.devices:
            topic = device.get("topic")
            name = device.get("name")
            success = self._send_single_ntfy(topic, title, message, tags="bell,white_check_mark", priority="high")
            results[name] = {"topic": topic, "delivered": success}

        # Also trigger local PC chime
        self.play_sound_and_toast(title, message)
        return {
            "success": any(r["delivered"] for r in results.values()),
            "devices_tested": len(self.devices),
            "results": results
        }


notifier = TradeNotifier()
