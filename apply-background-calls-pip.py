#!/usr/bin/env python3
"""Integrate native call foreground service and Android PiP into generated Capacitor project."""
from pathlib import Path
import re

root = Path("project")
java = root / "android/app/src/main/java/com/lnmusic/app"
manifest = root / "android/app/src/main/AndroidManifest.xml"
web = root / "www"
java.mkdir(parents=True, exist_ok=True)

(java / "CallSupportPlugin.java").write_text(r"""package com.lnmusic.app;
import android.content.Intent;
import android.os.Build;
import android.app.PictureInPictureParams;
import android.util.Rational;
import androidx.core.content.ContextCompat;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "CallSupport")
public class CallSupportPlugin extends Plugin {
  @PluginMethod public void start(PluginCall call) {
    boolean video = Boolean.TRUE.equals(call.getBoolean("video", false));
    MainActivity.callActive = true;
    try {
      Intent service = new Intent(getContext(), CallService.class).putExtra("video", video);
      ContextCompat.startForegroundService(getContext(), service);
      call.resolve();
    } catch (Exception e) {
      MainActivity.callActive = false;
      call.reject("Gagal mengaktifkan layanan panggilan: " + e.getMessage(), e);
    }
  }
  @PluginMethod public void stop(PluginCall call) {
    MainActivity.callActive = false;
    getContext().stopService(new Intent(getContext(), CallService.class));
    call.resolve();
  }
  @PluginMethod public void enterPip(PluginCall call) {
    if (Build.VERSION.SDK_INT < 26 || !MainActivity.callActive) {
      call.reject("PiP tidak tersedia atau panggilan tidak aktif"); return;
    }
    getActivity().runOnUiThread(() -> {
      try {
        getActivity().enterPictureInPictureMode(
          new PictureInPictureParams.Builder().setAspectRatio(new Rational(16, 9)).build());
      } catch (Exception e) { android.util.Log.w("LNMusic", "PiP gagal", e); }
    });
    call.resolve();
  }
}
""", encoding="utf-8")

(java / "CallService.java").write_text(r"""package com.lnmusic.app;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import androidx.core.app.NotificationCompat;

public class CallService extends Service {
  private static final String CHANNEL = "lnmusic_call";
  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    if (Build.VERSION.SDK_INT >= 26) {
      NotificationManager nm = getSystemService(NotificationManager.class);
      nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Panggilan LNMusic", NotificationManager.IMPORTANCE_LOW));
    }
    Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
    PendingIntent pi = PendingIntent.getActivity(this, 22, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    Notification n = new NotificationCompat.Builder(this, CHANNEL)
      .setSmallIcon(android.R.drawable.sym_call_incoming)
      .setContentTitle("Panggilan LNMusic aktif")
      .setContentText("Panggilan berjalan di latar belakang")
      .setCategory(NotificationCompat.CATEGORY_CALL).setOngoing(true).setContentIntent(pi).build();
    boolean video = intent != null && intent.getBooleanExtra("video", false);
    if (Build.VERSION.SDK_INT >= 29) {
      int types = ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
      if (video) types |= ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA;
      startForeground(22, n, types);
    } else startForeground(22, n);
    return START_NOT_STICKY;
  }
  @Override public IBinder onBind(Intent intent) { return null; }
}
""", encoding="utf-8")

# Patch MainActivity conservatively instead of replacing the existing keep-alive implementation.
main = java / "MainActivity.java"
s = main.read_text(encoding="utf-8")
if "CallSupportPlugin.class" not in s:
  s = s.replace("super.onCreate(savedInstanceState);", "registerPlugin(CallSupportPlugin.class);\n    super.onCreate(savedInstanceState);", 1)
if "static volatile boolean callActive" not in s:
  s = s.replace("public class MainActivity extends BridgeActivity {", "public class MainActivity extends BridgeActivity {\n  public static volatile boolean callActive = false;", 1)
if "onUserLeaveHint()" not in s:
  methods = r"""
  @Override public void onUserLeaveHint() {
    super.onUserLeaveHint();
    if (callActive && Build.VERSION.SDK_INT >= 26) {
      try {
        PictureInPictureParams.Builder b = new PictureInPictureParams.Builder().setAspectRatio(new Rational(16, 9));
        if (Build.VERSION.SDK_INT >= 31) b.setAutoEnterEnabled(true);
        enterPictureInPictureMode(b.build());
      } catch (Exception e) { android.util.Log.w("LNMusic", "Auto PiP gagal", e); }
    }
  }
  @Override public void onPictureInPictureModeChanged(boolean active, android.content.res.Configuration config) {
    super.onPictureInPictureModeChanged(active, config);
    try { getBridge().getWebView().evaluateJavascript("window.dispatchEvent(new CustomEvent('ln-pip',{detail:{active:" + active + "}}));", null); }
    catch (Exception ignored) {}
  }
"""
  s = s.rsplit("}", 1)[0] + methods + "}\n"
for imp in ["import android.app.PictureInPictureParams;", "import android.util.Rational;", "import android.os.Build;"]:
  if imp not in s:
    s = s.replace("import ", imp + "\nimport ", 1)
main.write_text(s, encoding="utf-8")

# Manifest: idempotently add permissions, PiP capability, and call service.
m = manifest.read_text(encoding="utf-8")
for p in ["FOREGROUND_SERVICE_MICROPHONE", "FOREGROUND_SERVICE_CAMERA"]:
  line = f'<uses-permission android:name="android.permission.{p}" />'
  if f"android.permission.{p}" not in m:
    m = m.replace("<application", line + "\n    <application", 1)
if 'android:name=".CallService"' not in m:
  m = m.replace("</application>", '<service android:name=".CallService" android:exported="false" android:foregroundServiceType="microphone|camera" />\n    </application>', 1)
m = re.sub(r'(<activity\\b[^>]*android:name="[^"]*MainActivity"[^>]*)(>)',
           lambda x: x.group(1) + (' android:supportsPictureInPicture="true"' if 'supportsPictureInPicture' not in x.group(1) else '') + x.group(2), m, count=1)
manifest.write_text(m, encoding="utf-8")

# Native JS bridge is appended to the bundled website. The normal web site remains unaffected.
bridge = r"""
/* LNMusic native call/PiP bridge */
(function () {
  var C = window.Capacitor;
  if (!C || !(C.isNativePlatform && C.isNativePlatform())) return;
  var P;
  try { P = (C.Plugins && C.Plugins.CallSupport) || C.registerPlugin('CallSupport'); } catch (_) { return; }
  window.lnCallStart = function (video) { return P.start({video: !!video}).catch(function(e){ console.warn(e); }); };
  window.lnCallStop = function () { return P.stop().catch(function(e){ console.warn(e); }); };
  window.lnEnterPip = function () { return P.enterPip().catch(function(e){ console.warn(e); }); };
  window.addEventListener('ln-pip', function(e) {
    document.body.classList.toggle('ln-native-pip', !!(e.detail && e.detail.active));
  });
})();
"""
native = web / "ln-native.js"
if native.exists():
  old = native.read_text(encoding="utf-8")
  if "LNMusic native call/PiP bridge" not in old:
    native.write_text(old + "\n" + bridge, encoding="utf-8")
# Wire foreground call service to the existing voice/video lifecycle in index.html.
idx = web / "index.html"
if idx.exists():
  h = idx.read_text(encoding="utf-8")
  h = h.replace("voiceOn = true; voiceMuted = voiceLocked; mutedByAdmin = false;",
                "voiceOn = true; voiceMuted = voiceLocked; mutedByAdmin = false; if (window.lnCallStart) window.lnCallStart(false);", 1)
  h = h.replace("voiceOn = true; voiceMuted = false; vcOn = true;",
                "voiceOn = true; voiceMuted = false; vcOn = true; if (window.lnCallStart) window.lnCallStart(true);", 1)
  h = h.replace("localStream = null; voiceOn = false;",
                "localStream = null; voiceOn = false; if (window.lnCallStop) window.lnCallStop();", 1)
  idx.write_text(h, encoding="utf-8")
print("Native call service + PiP integration applied.")
