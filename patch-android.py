#!/usr/bin/env python3
"""Apply the LNMusic native integration fixes to the extracted APK project."""
import json
import pathlib
import shutil
import sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "project")
overlay = root / "android-overlay"
config_path = root / "capacitor.config.json"

# The APK must load the website from its bundled www/ assets. A remote server.url
# makes the WebView origin differ from Capacitor's localhost bridge origin.
cfg = json.loads(config_path.read_text(encoding="utf-8"))
server = cfg.setdefault("server", {})
server.pop("url", None)
server["androidScheme"] = "https"
server.setdefault("allowNavigation", [
    "lnmusic.web.id", "*.lnmusic.web.id",
    "lnmusic.vercel.app", "*.vercel.app"
])
config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

LOCAL_PLUGIN = r'''package com.lnmusic.app;

import android.Manifest;
import android.content.ContentUris;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Set;

@CapacitorPlugin(name = "LocalMusic", permissions = {
  @Permission(strings = { Manifest.permission.READ_EXTERNAL_STORAGE }, alias = "storage"),
  @Permission(strings = { "android.permission.READ_MEDIA_AUDIO" }, alias = "audio")
})
public class LocalMusicPlugin extends Plugin {
  private static final HashMap<Long, MusicService.Item> LIB = new HashMap<>();

  @Override
  public void load() {
    MusicService.listener = new MusicService.StateListener() {
      @Override public void onState(JSObject s) { notifyListeners("state", s); }
    };
  }

  private String alias() { return Build.VERSION.SDK_INT >= 33 ? "audio" : "storage"; }
  private boolean ok() { return getPermissionState(alias()) == PermissionState.GRANTED; }

  @PluginMethod
  public void checkPerm(PluginCall call) {
    JSObject r = new JSObject();
    r.put("granted", ok());
    call.resolve(r);
  }

  @PluginMethod
  public void requestPerm(PluginCall call) {
    if (ok()) { checkPerm(call); return; }
    requestPermissionForAlias(alias(), call, "afterReq");
  }

  @PermissionCallback
  private void afterReq(PluginCall call) { checkPerm(call); }

  @PluginMethod
  public void scan(PluginCall call) {
    if (!ok()) { requestPermissionForAlias(alias(), call, "afterScan"); return; }
    doScan(call);
  }

  @PermissionCallback
  private void afterScan(PluginCall call) {
    if (ok()) doScan(call);
    else call.reject("Izin akses musik ditolak. Aktifkan izin Musik dan audio di Pengaturan Android.");
  }

  private void doScan(PluginCall call) {
    try {
      final int minDur = call.getInt("minDuration", 30000);
      final String selection = MediaStore.Audio.Media.IS_MUSIC + " != 0 AND "
        + MediaStore.Audio.Media.DURATION + " >= ?";
      final String[] selectionArgs = new String[] { String.valueOf(minDur) };
      final String[] projection = {
        MediaStore.Audio.Media._ID,
        MediaStore.Audio.Media.TITLE,
        MediaStore.Audio.Media.ARTIST,
        MediaStore.Audio.Media.ALBUM,
        MediaStore.Audio.Media.DURATION,
        MediaStore.Audio.Media.ALBUM_ID
      };

      List<String> volumes = new ArrayList<>();
      if (Build.VERSION.SDK_INT >= 29) {
        Set<String> names = MediaStore.getExternalVolumeNames(getContext());
        volumes.addAll(names);
        if (volumes.isEmpty()) volumes.add(MediaStore.VOLUME_EXTERNAL_PRIMARY);
        Collections.sort(volumes, new Comparator<String>() {
          @Override public int compare(String a, String b) {
            boolean ap = MediaStore.VOLUME_EXTERNAL_PRIMARY.equals(a);
            boolean bp = MediaStore.VOLUME_EXTERNAL_PRIMARY.equals(b);
            if (ap == bp) return a.compareToIgnoreCase(b);
            return ap ? -1 : 1;
          }
        });
      } else {
        volumes.add("external");
      }

      ArrayList<MusicService.Item> found = new ArrayList<>();
      int queriedVolumes = 0;
      for (String volume : volumes) {
        Uri audioBase = Build.VERSION.SDK_INT >= 29
          ? MediaStore.Audio.Media.getContentUri(volume)
          : MediaStore.Audio.Media.EXTERNAL_CONTENT_URI;
        try (Cursor cursor = getContext().getContentResolver().query(
            audioBase, projection, selection, selectionArgs,
            MediaStore.Audio.Media.TITLE + " COLLATE NOCASE ASC")) {
          if (cursor == null) continue;
          queriedVolumes++;
          while (cursor.moveToNext()) {
            MusicService.Item item = new MusicService.Item();
            long mediaId = cursor.getLong(0);
            item.id = mediaId; // diganti menjadi ID antrean unik setelah semua volume dipindai
            item.title = cursor.getString(1) == null ? "Tanpa judul" : cursor.getString(1);
            String artist = cursor.getString(2);
            item.artist = (artist == null || artist.equals("<unknown>")) ? "Artis tidak dikenal" : artist;
            item.album = cursor.getString(3) == null ? "" : cursor.getString(3);
            item.dur = cursor.getLong(4);
            item.albumId = cursor.getLong(5);
            item.volumeName = volume;
            item.storage = (Build.VERSION.SDK_INT < 29 || MediaStore.VOLUME_EXTERNAL_PRIMARY.equals(volume))
              ? "Memori Internal" : "Kartu SD";
            item.uri = ContentUris.withAppendedId(audioBase, mediaId);
            if (Build.VERSION.SDK_INT >= 29) {
              Uri albumBase = MediaStore.Audio.Albums.getContentUri(volume);
              item.albumUri = ContentUris.withAppendedId(albumBase, item.albumId);
            } else {
              item.albumUri = ContentUris.withAppendedId(
                Uri.parse("content://media/external/audio/albums"), item.albumId);
            }
            found.add(item);
          }
        } catch (SecurityException volumeError) {
          // Volume yang tidak tersedia/tidak dapat dibaca tidak boleh menggagalkan volume lain.
        }
      }

      Collections.sort(found, new Comparator<MusicService.Item>() {
        @Override public int compare(MusicService.Item a, MusicService.Item b) {
          return a.title.compareToIgnoreCase(b.title);
        }
      });

      JSArray items = new JSArray();
      LIB.clear();
      long queueId = 1;
      for (MusicService.Item item : found) {
        item.id = queueId++;
        LIB.put(item.id, item);
        JSObject row = new JSObject();
        row.put("id", item.id);
        row.put("title", item.title);
        row.put("artist", item.artist);
        row.put("album", item.album);
        row.put("dur", item.dur);
        row.put("storage", item.storage);
        items.put(row);
      }

      JSObject result = new JSObject();
      result.put("items", items);
      result.put("locations", queriedVolumes);
      call.resolve(result);
    } catch (Exception e) {
      call.reject("Gagal memindai musik: " + e.getMessage(), e);
    }
  }

  @PluginMethod
  public void play(PluginCall call) {
    JSArray ids = call.getArray("ids");
    if (ids == null) { call.reject("Daftar lagu kosong"); return; }
    int index = call.getInt("index", 0);
    ArrayList<MusicService.Item> list = new ArrayList<>();
    MusicService.Item start = null;
    for (int i = 0; i < ids.length(); i++) {
      try {
        MusicService.Item item = LIB.get(ids.getLong(i));
        if (item == null) continue;
        list.add(item);
        if (i == index) start = item;
      } catch (Exception ignored) {}
    }
    if (list.isEmpty()) { call.reject("Lagu tidak ditemukan. Silakan pindai ulang."); return; }
    MusicService.queueIn = list;
    MusicService.startIndexIn = start == null ? 0 : Math.max(0, list.indexOf(start));
    try {
      ContextCompat.startForegroundService(getContext(),
        new Intent(getContext(), MusicService.class).setAction(MusicService.A_START));
      call.resolve();
    } catch (Exception e) { call.reject("Gagal memulai pemutar: " + e.getMessage(), e); }
  }

  @PluginMethod public void pause(PluginCall call) { MusicService.exec(MusicService.A_PAUSE, 0); call.resolve(); }
  @PluginMethod public void resume(PluginCall call) { MusicService.exec(MusicService.A_RESUME, 0); call.resolve(); }
  @PluginMethod public void toggle(PluginCall call) { MusicService.exec(MusicService.A_TOGGLE, 0); call.resolve(); }
  @PluginMethod public void next(PluginCall call) { MusicService.exec(MusicService.A_NEXT, 0); call.resolve(); }
  @PluginMethod public void prev(PluginCall call) { MusicService.exec(MusicService.A_PREV, 0); call.resolve(); }
  @PluginMethod public void stop(PluginCall call) { MusicService.exec(MusicService.A_STOP, 0); call.resolve(); }

  @PluginMethod
  public void seek(PluginCall call) {
    MusicService.exec(MusicService.A_SEEK, call.getInt("ms", 0));
    call.resolve();
  }

  @PluginMethod
  public void setMode(PluginCall call) {
    boolean shuffle = Boolean.TRUE.equals(call.getBoolean("shuffle", false));
    int repeat = call.getInt("repeat", 0);
    MusicService.exec(MusicService.A_MODE, (shuffle ? 4 : 0) | (repeat & 3));
    call.resolve();
  }

  @PluginMethod
  public void getState(PluginCall call) { call.resolve(MusicService.snapshot()); }
}
'''

plugin_path = overlay / "LocalMusicPlugin.java"
plugin_path.write_text(LOCAL_PLUGIN, encoding="utf-8")

music_path = overlay / "MusicService.java"
music = music_path.read_text(encoding="utf-8")
old_item = "    public String title, artist, album;\n    public Uri uri;"
new_item = "    public String title, artist, album, volumeName, storage;\n    public Uri uri, albumUri;"
if old_item not in music:
    raise SystemExit("MusicService.Item declaration changed; refusing unsafe patch")
music = music.replace(old_item, new_item, 1)
old_art = '          Uri a = ContentUris.withAppendedId(Uri.parse("content://media/external/audio/albums"), it.albumId);'
new_art = '          Uri a = it.albumUri != null ? it.albumUri : ContentUris.withAppendedId(Uri.parse("content://media/external/audio/albums"), it.albumId);'
if old_art not in music:
    raise SystemExit("MusicService album-art URI changed; refusing unsafe patch")
music = music.replace(old_art, new_art, 1)
music_path.write_text(music, encoding="utf-8")

# Make manifest permissions/services idempotent and independent of one another.
apply_py = r'''import pathlib
import shutil

base = pathlib.Path("android/app/src/main")
dst = base / "java/com/lnmusic/app"
dst.mkdir(parents=True, exist_ok=True)

for name in ["MainActivity.java", "PlayService.java", "BackgroundPlayPlugin.java", "MusicService.java", "LocalMusicPlugin.java"]:
    shutil.copy("android-overlay/" + name, dst / name)

manifest = base / "AndroidManifest.xml"
text = manifest.read_text(encoding="utf-8")
permissions = [
    ('INTERNET', None),
    ('ACCESS_NETWORK_STATE', None),
    ('CAMERA', None),
    ('RECORD_AUDIO', None),
    ('MODIFY_AUDIO_SETTINGS', None),
    ('POST_NOTIFICATIONS', None),
    ('WAKE_LOCK', None),
    ('FOREGROUND_SERVICE', None),
    ('FOREGROUND_SERVICE_MEDIA_PLAYBACK', None),
    ('READ_EXTERNAL_STORAGE', '32'),
    ('READ_MEDIA_AUDIO', None),
]
additions = []
for permission, max_sdk in permissions:
    full = "android.permission." + permission
    if full not in text:
        suffix = ' android:maxSdkVersion="' + max_sdk + '"' if max_sdk else ''
        additions.append('    <uses-permission android:name="' + full + '"' + suffix + ' />')
for feature in ["android.hardware.camera", "android.hardware.microphone"]:
    if feature not in text:
        additions.append('    <uses-feature android:name="' + feature + '" android:required="false" />')
if additions:
    marker = "<application"
    if marker not in text:
        raise SystemExit("AndroidManifest.xml has no application element")
    text = text.replace(marker, "\n".join(additions) + "\n    " + marker, 1)

services = [
    (".PlayService", '        <service android:name=".PlayService" android:exported="false" android:foregroundServiceType="mediaPlayback" />'),
    (".MusicService", '        <service android:name=".MusicService" android:exported="false" android:foregroundServiceType="mediaPlayback" />'),
]
for name, declaration in services:
    if 'android:name="' + name + '"' not in text:
        text = text.replace("</application>", declaration + "\n    </application>", 1)
manifest.write_text(text, encoding="utf-8")

shutil.copytree("android-overlay/res", base / "res", dirs_exist_ok=True)
print("LNMusic Android overlay, media permissions, and services applied.")
'''
(overlay / "apply.py").write_text(apply_py, encoding="utf-8")

print("Patched local Capacitor asset mode and multi-volume MediaStore scanner.")
