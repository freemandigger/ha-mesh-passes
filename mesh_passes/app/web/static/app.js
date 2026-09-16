const $ = (id) => document.getElementById(id);

const STATUS = {
  ok: "работает",
  auth_required: "нужен вход",
  api_error: "ошибка API МЭШ",
  outside_hours: "вне окна опроса",
};

const SCANNER = "«Моя Москва» или «Госуслуги Москвы»: Настройки → Безопасность → сканер QR-кода.";
const TEXT = {
  logged_out: `Нажмите кнопку и отсканируйте QR-код в приложении ${SCANNER}`,
  auth_required: "Сессия mos.ru закончилась — войдите заново.",
  qr: `Отсканируйте QR-код в приложении ${SCANNER} Код обновляется сам.`,
  confirm: "Подтвердите вход в приложении на телефоне.",
  sms: "mos.ru видит новое устройство — введите код из SMS.",
};

const moscowTime = (iso) => (iso ? new Date(iso).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) : "—");

function showError(message) {
  $("error").hidden = !message;
  $("error").textContent = message || "";
}

async function call(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  render(await response.json());
}

function renderChild(child) {
  const box = document.createElement("div");
  const title = document.createElement("h2");
  const place = child.at_school === null ? "нет данных" : child.at_school ? "в школе" : "не в школе";
  title.textContent = `${child.name}${child.class_name ? ` (${child.class_name})` : ""} — ${place}`;
  const visits = document.createElement("p");
  visits.className = "muted";
  visits.textContent = child.visits.length
    ? child.visits.map((visit) => `вход ${visit.in}, выход ${visit.out}`).join("; ")
    : "Сегодня проходов нет";
  box.append(title, visits);
  return box;
}

function render(s) {
  $("login").hidden = s.state === "logged_in";
  $("login-text").textContent = TEXT[s.state] || "";
  $("qr").hidden = s.state !== "qr";
  if (s.state === "qr" && $("qr").dataset.svg !== s.qr_svg) {
    $("qr").innerHTML = s.qr_svg;
    $("qr").dataset.svg = s.qr_svg;
  }
  $("sms").hidden = s.state !== "sms";
  if (s.sms) {
    const left = `${Math.floor(s.sms.seconds_left / 60)}:${String(s.sms.seconds_left % 60).padStart(2, "0")}`;
    $("sms-info").textContent = `Код действует ещё ${left}` + (s.sms.attempts != null ? `, попыток: ${s.sms.attempts}` : "");
  }
  $("start").hidden = !["logged_out", "auth_required"].includes(s.state);
  showError(s.error);
  for (const button of document.querySelectorAll("button")) button.disabled = s.busy;
  $("session").hidden = s.state !== "logged_in" && s.children.length === 0;
  $("status").textContent = STATUS[s.status] || s.status;
  $("last-poll").textContent = moscowTime(s.last_poll);
  $("token-expires").textContent = moscowTime(s.token_expires);
  $("last-renewal").textContent = moscowTime(s.last_renewal);
  $("children").hidden = s.children.length === 0;
  $("children").replaceChildren(...s.children.map(renderChild));
}

$("start").onclick = () => call("api/login/start", {}).catch((err) => showError(err.message));
$("sms").onsubmit = (event) => {
  event.preventDefault();
  call("api/login/sms", { code: $("code").value })
    .then(() => { $("code").value = ""; })
    .catch((err) => showError(err.message));
};
$("resend").onclick = () => call("api/login/sms/resend", {}).catch((err) => showError(err.message));
$("poll").onclick = () => call("api/poll", {}).catch((err) => showError(err.message));
$("logout").onclick = () => {
  if (confirm("Выйти из mos.ru? Для нового входа понадобится QR-код и, возможно, SMS.")) {
    call("api/logout", {}).catch((err) => showError(err.message));
  }
};

async function tick() {
  try {
    await call("api/status");
  } catch (err) {
    showError(err.message);
  }
  setTimeout(tick, 2000);
}

tick();
