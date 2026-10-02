-- Push-to-talk dictation for every macOS app, using ChatGPT's transcribe endpoint.
-- Hold Right Option to talk; release it and the text is pasted at the cursor.
-- Pressing any other key while holding it (i.e. using an Option+key shortcut) cancels the take.
--
-- The mic only opens when you use it: the first press opens it (~0.5 s, the HUD shows "Opening mic…"), then
-- dictation-daemon.py keeps it open for KEEP_WARM_SEC so later takes include PREROLL_SEC of audio from before
-- the key press (the first word is never lost). After that, or when the screen locks or the Mac sleeps, the
-- mic closes and the orange dot goes away. Audio stays in RAM and is only sent when you release the key. With
-- a Bluetooth headset the mic closes right after each take, because keeping a Bluetooth mic open forces the
-- headset into call mode (music sounds bad).
-- While the key is held, the speakers/headphones are muted so other sounds don't leak into the mic.
-- A HUD at the bottom of the screen shows a live level meter: flat bars mean the mic hears nothing.
--
-- The Codex login token is refreshed through codex_auth.py (checked every 6 hours and on wake).
--
-- Load it from ~/.hammerspoon/init.lua (install.sh adds this line):
--   dofile("/path/to/chatgpt-dictation/dictation.lua")

local M = {}

local DIR = debug.getinfo(1, "S").source:sub(2):match("(.*/)")
local PYTHON = "/usr/bin/python3"
local DAEMON = DIR .. "dictation-daemon.py"
local AUTH = DIR .. "codex_auth.py"
local MIC = ":default" -- system default input; list devices with: ffmpeg -f avfoundation -list_devices true -i ""
local KEEP_WARM_SEC = 300 -- keep the mic open after the last take; 0 = close at once, math.huge = never close
local PREROLL_SEC = 0.5
local MIN_MS = 400 -- holds shorter than this count as accidental presses
local MAX_SEC = 120 -- stop automatically if the key is never released
local REFRESH_UNDER_HOURS = 72 -- tokens live 10 days; ask Codex to refresh once fewer than 3 days are left
local REFRESH_CHECK_SEC = 6 * 3600
local RIGHT_OPTION = hs.eventtap.event.rawFlagMasks.deviceRightAlternate
local types = hs.eventtap.event.types

local LISTENING = "Listening… release Right Option to stop"
local HUD_W, HUD_H_BARS, HUD_H_TEXT, HUD_BOTTOM_GAP, BARS = 340, 72, 44, 48, 36
local DOT_COLORS = {
  opening = { white = 0.55 }, -- mic is opening, not listening yet
  listening = { red = 1, green = 0.27, blue = 0.23 },
  busy = { red = 0.35, green = 0.65, blue = 1 }, -- transcribing
  done = { red = 0.3, green = 0.85, blue = 0.4 },
  error = { red = 1, green = 0.6, blue = 0.2 },
}
-- At or below the floor the bars are flat; at or above the ceiling they are full. Measured on a long real take:
-- silence around -60 to -45 dB, normal speech -40 to -28 dB, so normal speech fills about 50–80% of a bar.
local LEVEL_FLOOR_DB, LEVEL_CEIL_DB = -50, -25
local daemon, awaitingReady, inFlight = nil, false, 0
local recording, startedAt, maxTimer, closeTimer
local restoreTimer, refreshTask -- keep references: a timer/task nobody holds is garbage-collected before it runs
local mutedOutput -- output device this take muted; only unmute a device we muted ourselves

local hud, hudTimer, levels = nil, nil, {}

local function barFrame(i, level)
  local slot = (HUD_W - 32) / BARS
  local h = 2 + level * 24
  return { x = 16 + (i - 1) * slot, y = 50 - h / 2, w = slot - 2, h = h }
end

local function hideHud()
  if hudTimer then hudTimer:stop(); hudTimer = nil end
  if hud then hud:hide() end
end

-- One HUD at the bottom of the screen for the whole take: opening/listening show the level meter, busy/done/error
-- show text only. The top edge stays put so the text doesn't jump between states. seconds: hide after that long.
local function showHud(state, text, seconds)
  if not hud then
    hud = hs.canvas.new({ x = 0, y = 0, w = HUD_W, h = HUD_H_BARS })
    hud:level(hs.canvas.windowLevels.overlay)
    hud:behaviorAsLabels({ "canJoinAllSpaces", "stationary" })
    hud[1] = { type = "rectangle", action = "fill", fillColor = { white = 0.08, alpha = 0.85 },
      roundedRectRadii = { xRadius = 14, yRadius = 14 } }
    hud[2] = { type = "circle", action = "fill", center = { x = 24, y = 22 }, radius = 5 }
    hud[3] = { type = "text", textSize = 13, textColor = { white = 1 }, textLineBreak = "truncateTail" }
    for i = 1, BARS do
      hud[3 + i] = { type = "rectangle", action = "fill", fillColor = { white = 1, alpha = 0.9 } }
    end
  end
  if hudTimer then hudTimer:stop(); hudTimer = nil end
  local withBars = state == "opening" or state == "listening"
  local f = hs.screen.mainScreen():frame()
  local w = math.min(f.w - 32, math.max(HUD_W, hs.drawing.getTextDrawingSize(text, { size = 13 }).w + 56))
  hud:frame({ x = f.x + (f.w - w) / 2, y = f.y + f.h - HUD_BOTTOM_GAP - HUD_H_BARS,
    w = w, h = withBars and HUD_H_BARS or HUD_H_TEXT })
  hud[2].fillColor = DOT_COLORS[state]
  hud[3].text = text
  hud[3].frame = { x = 38, y = 13, w = w - 50, h = 20 }
  for i = 1, BARS do
    levels[i] = 0
    hud[3 + i].action = withBars and "fill" or "skip"
    hud[3 + i].frame = barFrame(i, 0)
  end
  hud:show()
  if seconds then hudTimer = hs.timer.doAfter(seconds, hideHud) end
end

-- Messages from an earlier take must not cover the HUD of the take being recorded; they still reach the Console.
local function notify(state, text, seconds)
  if not recording then showHud(state, text, seconds) end
end

-- Newest bar on the right; older bars scroll to the left.
local function pushLevel(db)
  if not hud then return end
  table.remove(levels, 1)
  levels[BARS] = math.min(1, math.max(0, (db - LEVEL_FLOOR_DB) / (LEVEL_CEIL_DB - LEVEL_FLOOR_DB)))
  for i = 1, BARS do hud[3 + i].frame = barFrame(i, levels[i]) end
end

local function pasteText(text)
  local saved = hs.pasteboard.readAllData()
  hs.pasteboard.setContents(text)
  hs.eventtap.keyStroke({ "cmd" }, "v", 0)
  restoreTimer = hs.timer.doAfter(0.8, function()
    if saved and next(saved) then hs.pasteboard.writeAllData(saved) end
  end)
end

local function muteOutput()
  local device = hs.audiodevice.defaultOutputDevice()
  if device and not device:outputMuted() and device:setOutputMuted(true) then mutedOutput = device end
end

-- Call this on every path that ends a take, or the Mac stays muted.
local function restoreOutput()
  if mutedOutput then mutedOutput:setOutputMuted(false); mutedOutput = nil end
end

-- Default output: paste at the cursor. Can be replaced (e.g. in tests) with any function that takes the text.
M.output = function(text)
  pasteText(text)
  notify("done", "Pasted", 0.8)
end

local function daemonRunning() return daemon ~= nil and daemon:isRunning() end

local function send(command)
  if daemonRunning() then daemon:setInput(command .. "\n") end
end

local function stopDaemon()
  if closeTimer then closeTimer:stop(); closeTimer = nil end
  local task = daemon
  daemon, awaitingReady, inFlight = nil, false, 0 -- cleared first so the exit callback knows the stop was intended
  if task and task:isRunning() then task:terminate() end
end

local function closeMicWhenIdle()
  if closeTimer then closeTimer:stop(); closeTimer = nil end
  if recording or inFlight > 0 or not daemonRunning() then return end
  local device = hs.audiodevice.defaultInputDevice()
  local warm = (device and device:transportType() == "Bluetooth") and 0 or KEEP_WARM_SEC
  if warm == 0 then
    stopDaemon()
  elseif warm < math.huge then
    closeTimer = hs.timer.doAfter(warm, function()
      closeTimer = nil
      if not recording and inFlight == 0 then stopDaemon() end
    end)
  end
end

local function handleEvent(event)
  if event.event == "level" then
    if recording then pushLevel(event.db) end
  elseif event.event == "ready" then
    if awaitingReady and recording then showHud("listening", LISTENING) end
    awaitingReady = false
  elseif event.event == "text" or event.event == "error" then
    inFlight = math.max(0, inFlight - 1)
    if event.event == "text" then
      print("[dictation] " .. event.text)
      M.output(event.text)
    else
      print("[dictation] error: " .. tostring(event.message))
      notify("error", "Dictation error: " .. tostring(event.message), 4)
    end
    closeMicWhenIdle()
  elseif event.event == "mic_lost" then
    print("[dictation] " .. tostring(event.message))
    notify("error", "Dictation error: " .. tostring(event.message), 4)
  end
end

local function startDaemon()
  local task, buffered = nil, ""
  task = hs.task.new(PYTHON, function(_, _, err)
    if daemon ~= task then return end -- stopped on purpose or already replaced
    daemon = nil
    if err and err ~= "" then print("[dictation] daemon exited: " .. err) end
    if recording or inFlight > 0 then
      recording, inFlight = false, 0
      restoreOutput()
      showHud("error", "Dictation error: the mic stopped mid-recording, please try again", 4)
    end
  end, function(_, out, err)
    if daemon ~= task then return true end
    buffered = buffered .. (out or "")
    while true do
      local line, rest = buffered:match("^(.-)\n(.*)$")
      if not line then break end
      buffered = rest
      local ok, event = pcall(hs.json.decode, line)
      if ok and type(event) == "table" then handleEvent(event) end
    end
    if err and err ~= "" then print("[dictation] " .. err) end
    return true
  end, { DAEMON, "--preroll", tostring(PREROLL_SEC), "--mic", MIC })
  daemon, awaitingReady, inFlight = task, true, 0
  task:start()
end

local function startRecording()
  if recording then return end
  if closeTimer then closeTimer:stop(); closeTimer = nil end
  if not daemonRunning() then startDaemon() end
  recording, startedAt = true, hs.timer.absoluteTime()
  send("start")
  muteOutput()
  maxTimer = hs.timer.doAfter(MAX_SEC, function() M.stop() end)
  if awaitingReady then showHud("opening", "Opening mic…") else showHud("listening", LISTENING) end
end

M.start = startRecording

function M.stop(cancel)
  if not recording then return end
  recording = false
  if maxTimer then maxTimer:stop(); maxTimer = nil end
  local ms = (hs.timer.absoluteTime() - startedAt) / 1e6
  if cancel or ms < MIN_MS then
    send("cancel")
    hideHud()
  else
    send("stop")
    inFlight = inFlight + 1
    showHud("busy", "Transcribing…", 60)
  end
  restoreOutput()
  closeMicWhenIdle()
end

M.tap = hs.eventtap.new({ types.flagsChanged, types.keyDown }, function(event)
  if event:getType() == types.keyDown then
    M.stop(true)
  elseif (event:rawFlags() & RIGHT_OPTION) ~= 0 then
    startRecording()
  else
    M.stop()
  end
  return false
end)
M.tap:start()

local function refreshToken()
  if refreshTask and refreshTask:isRunning() then return end
  refreshTask = hs.task.new(PYTHON, function(code, out, err)
    local message = ((code == 0 and out or err) or ""):gsub("%s+$", "")
    print("[dictation] token: " .. message)
    if code ~= 0 then notify("error", "Dictation: couldn't refresh the Codex token. " .. message, 6) end
  end, { AUTH, "--refresh-under", tostring(REFRESH_UNDER_HOURS) })
  refreshTask:start()
end
M.refreshTimer = hs.timer.doEvery(REFRESH_CHECK_SEC, refreshToken)
M.firstRefresh = hs.timer.doAfter(10, refreshToken)

-- Default mic changed: stop the daemon (it holds the old mic); the next press opens the new one.
local function releaseMic()
  if recording or inFlight > 0 then return end
  stopDaemon()
end
hs.audiodevice.watcher.setCallback(function(event)
  if event == "dIn " then releaseMic() end
end)
hs.audiodevice.watcher.start()
M.power = hs.caffeinate.watcher.new(function(event)
  local w = hs.caffeinate.watcher
  if event == w.screensDidLock or event == w.systemWillSleep then releaseMic() end
  if event == w.systemDidWake then refreshToken() end
end):start()

-- Hammerspoon reload/quit: unmute if a take was in progress, and stop the daemon so no process is left behind.
hs.shutdownCallback = function()
  restoreOutput()
  stopDaemon()
end

M.status = function()
  return string.format("mic_open=%s recording=%s in_flight=%d close_timer=%s",
    tostring(daemonRunning()), tostring(recording), inFlight, tostring(closeTimer ~= nil))
end
_G.chatgptDictation = M -- keep a reference so the eventtap, watchers and timers aren't garbage-collected
return M
