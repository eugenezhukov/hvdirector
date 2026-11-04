// VM console page (loaded in an iframe or a separate window).
// guacamole-common-js <-> /ws/console (HV Director tunnel) <-> guacd <-> Hyper-V host:2179 (vmconnect)
import Guacamole from 'guacamole-common-js'
import './console.css'

const params = new URLSearchParams(window.location.search)
const lang = params.get('lang') === 'en' ? 'en' : 'ru'
const T = {
  ru: {
    connecting: 'Подключение…', waiting: 'Ожидание ответа сервера…', connected: 'Подключено',
    disconnected: 'Консоль отключена', reconnect: 'Переподключиться', fit: 'Вписать', actual: '1:1',
    fullscreen: 'Во весь экран', noTicket: 'Нет тикета консоли. Откройте консоль из портала.',
    ticketUsed: 'Тикет консоли одноразовый: для переподключения откройте консоль из портала заново.',
  },
  en: {
    connecting: 'Connecting…', waiting: 'Waiting for server…', connected: 'Connected',
    disconnected: 'Console disconnected', reconnect: 'Reconnect', fit: 'Fit', actual: '1:1',
    fullscreen: 'Full screen', noTicket: 'No console ticket. Open the console from the portal.',
    ticketUsed: 'Console tickets are one-time: open the console from the portal again to reconnect.',
  },
}[lang]

const $ = (id) => document.getElementById(id)
const screenEl = $('screen')
const statusEl = $('status')
const overlay = $('overlay')
const overlayText = $('overlay-text')
const btnFit = $('btn-fit')
const btnFull = $('btn-full')
const btnReconnect = $('btn-reconnect')

const vmName = params.get('name') || ''
$('vm-name').textContent = vmName
document.title = `${vmName} — Console`
btnFit.textContent = T.actual
btnFull.textContent = T.fullscreen
btnReconnect.textContent = T.reconnect

let fit = true
let client = null
let keyboard = null
let usedTicket = false

function setStatus(text, cls) {
  statusEl.textContent = text
  statusEl.className = 'status ' + (cls || '')
}

function showOverlay(text) {
  overlayText.textContent = text
  overlay.style.display = text ? 'flex' : 'none'
}

function rescale() {
  if (!client) return
  const display = client.getDisplay()
  const w = display.getWidth()
  const h = display.getHeight()
  if (!w || !h) return
  const scale = fit ? Math.min(screenEl.clientWidth / w, screenEl.clientHeight / h, 1) : 1
  display.scale(scale)
}

function connect() {
  const ticket = params.get('ticket')
  if (!ticket) {
    showOverlay(T.noTicket)
    return
  }
  if (usedTicket) {
    showOverlay(T.ticketUsed)
    return
  }
  usedTicket = true
  btnReconnect.hidden = true
  const tunnel = new Guacamole.WebSocketTunnel('/ws/console')
  client = new Guacamole.Client(tunnel)
  const display = client.getDisplay()
  screenEl.innerHTML = ''
  screenEl.appendChild(display.getElement())

  client.onstatechange = (state) => {
    // 0 IDLE, 1 CONNECTING, 2 WAITING, 3 CONNECTED, 4 DISCONNECTING, 5 DISCONNECTED
    if (state === 1) setStatus(T.connecting, 'busy')
    if (state === 2) setStatus(T.waiting, 'busy')
    if (state === 3) {
      setStatus(T.connected, 'ok')
      showOverlay('')
      screenEl.focus()
    }
    if (state === 5) {
      setStatus(T.disconnected, 'err')
      if (overlay.style.display !== 'flex') showOverlay(T.disconnected)
      btnReconnect.hidden = false
    }
  }
  client.onerror = (status) => {
    setStatus(T.disconnected, 'err')
    showOverlay(status && status.message ? status.message : T.disconnected)
    btnReconnect.hidden = false
  }
  display.onresize = rescale

  const mouse = new Guacamole.Mouse(display.getElement())
  // Use the remote cursor image as the local CSS cursor (falls back to a drawn cursor layer).
  display.oncursor = (canvas, x, y) => {
    const local = mouse.setCursor(canvas, x, y)
    display.showCursor(!local)
  }
  if (typeof mouse.onEach === 'function') {
    mouse.onEach(['mousedown', 'mousemove', 'mouseup'], (e) => client.sendMouseState(e.state, true))
  } else {
    const send = (state) => {
      const s = display.getScale() || 1
      client.sendMouseState(new Guacamole.Mouse.State(state.x / s, state.y / s, state.left, state.middle,
        state.right, state.up, state.down))
    }
    mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = send
  }

  if (!keyboard) {
    keyboard = new Guacamole.Keyboard(screenEl)
    keyboard.onkeydown = (keysym) => {
      if (client) client.sendKeyEvent(1, keysym)
      return false
    }
    keyboard.onkeyup = (keysym) => {
      if (client) client.sendKeyEvent(0, keysym)
    }
  }

  const ratio = window.devicePixelRatio || 1
  const width = Math.max(800, Math.round(screenEl.clientWidth * ratio))
  const height = Math.max(600, Math.round(screenEl.clientHeight * ratio))
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''
  const data = new URLSearchParams({ ticket, width: String(width), height: String(height),
    dpi: String(Math.round(96 * ratio)), timezone: tz })
  setStatus(T.connecting, 'busy')
  showOverlay(T.connecting)
  client.connect(data.toString())
}

function sendCtrlAltDel() {
  if (!client) return
  const CTRL = 0xffe3
  const ALT = 0xffe9
  const DEL = 0xffff
  client.sendKeyEvent(1, CTRL)
  client.sendKeyEvent(1, ALT)
  client.sendKeyEvent(1, DEL)
  client.sendKeyEvent(0, DEL)
  client.sendKeyEvent(0, ALT)
  client.sendKeyEvent(0, CTRL)
  screenEl.focus()
}

$('btn-cad').addEventListener('click', sendCtrlAltDel)
btnFit.addEventListener('click', () => {
  fit = !fit
  btnFit.textContent = fit ? T.actual : T.fit
  rescale()
})
btnFull.addEventListener('click', () => {
  const el = document.documentElement
  if (document.fullscreenElement) document.exitFullscreen()
  else if (el.requestFullscreen) el.requestFullscreen()
})
btnReconnect.addEventListener('click', () => showOverlay(T.ticketUsed))
screenEl.addEventListener('mousedown', () => screenEl.focus())
window.addEventListener('resize', rescale)
window.addEventListener('message', (ev) => {
  if (ev.origin !== window.location.origin) return
  if (ev.data === 'ctrl-alt-del') sendCtrlAltDel()
})
window.addEventListener('beforeunload', () => {
  if (client) client.disconnect()
})

connect()
