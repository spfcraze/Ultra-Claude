"""
System notification module for alerting when sessions need attention
"""
import platform
import subprocess
from typing import Optional


class Notifier:
    def __init__(self):
        self.system = platform.system()

    def notify(self, title: str, message: str, urgency: str = "normal") -> bool:
        """
        Send a system notification

        Args:
            title: Notification title
            message: Notification body
            urgency: low, normal, or critical

        Returns:
            True if notification was sent successfully
        """
        try:
            if self.system == "Linux":
                return self._notify_linux(title, message, urgency)
            elif self.system == "Darwin":
                return self._notify_macos(title, message)
            elif self.system == "Windows":
                return self._notify_windows(title, message)
            else:
                print(f"[NOTIFY] {title}: {message}")
                return True
        except Exception as e:
            print(f"Notification error: {e}")
            return False

    def _notify_linux(self, title: str, message: str, urgency: str) -> bool:
        """Send notification on Linux using notify-send"""
        try:
            subprocess.run([
                "notify-send",
                "-u", urgency,
                "-a", "Autowrkers",
                title,
                message
            ], check=True, capture_output=True)
            return True
        except FileNotFoundError:
            # Try using plyer as fallback
            try:
                from plyer import notification
                notification.notify(
                    title=title,
                    message=message,
                    app_name="Autowrkers",
                    timeout=10
                )
                return True
            except:
                print(f"[NOTIFY] {title}: {message}")
                return False

    def _notify_macos(self, title: str, message: str) -> bool:
        """Send notification on macOS using osascript"""
        script = f'''
        display notification "{message}" with title "{title}" sound name "default"
        '''
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        return True

    def _notify_windows(self, title: str, message: str) -> bool:
        """Send notification on Windows"""
        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message,
                app_name="Autowrkers",
                timeout=10
            )
            return True
        except ImportError:
            # Fallback to PowerShell toast
            ps_script = f'''
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $textNodes = $template.GetElementsByTagName("text")
            $textNodes.Item(0).AppendChild($template.CreateTextNode("{title}")) | Out-Null
            $textNodes.Item(1).AppendChild($template.CreateTextNode("{message}")) | Out-Null
            $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Autowrkers").Show($toast)
            '''
            try:
                subprocess.run(["powershell", "-Command", ps_script], check=True, capture_output=True)
                return True
            except:
                print(f"[NOTIFY] {title}: {message}")
                return False


# Global instance
notifier = Notifier()


def notify_session_needs_attention(session_name: str, session_id: int):
    """Convenience function for session attention notifications"""
    notifier.notify(
        title=f"Autowrkers - {session_name}",
        message=f"Session #{session_id} needs your input",
        urgency="normal"
    )
