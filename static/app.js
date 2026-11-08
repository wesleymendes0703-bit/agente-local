"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const est = { conversa: null, imagem: null, nome: "Levi", ultimoAviso: 0, gravador: null, enviando: false };

// ---------- utilidades ----------
async function api(caminho, opcoes = {}) {
  const o = { headers: {}, ...opcoes };
  if (o.corpo !== undefined) {
    o.body = JSON.stringify(o.corpo);
    o.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(caminho, o);
  if (r.status === 401 && !caminho.startsWith("/api/entrar") && !caminho.startsWith("/api/senha")) {
    mostrarAcesso(true);
    throw new Error("entre com sua senha");
  }
  const dados = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(dados.detail || `erro ${r.status}`);
  return dados;
}

function avisar(texto, ms = 3500) {
  const a = $("#aviso");
  a.textContent = texto;
  a.classList.remove("oculto");
  clearTimeout(avisar.t);
  avisar.t = setTimeout(() => a.classList.add("oculto"), ms);
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function md(texto) {
  // markdown pequeno e seguro: escapa tudo primeiro
  let t = esc(texto);
  const blocos = [];
  t = t.replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, c) => { blocos.push(c); return `\u0000${blocos.length - 1}\u0000`; });
  t = t.replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>')
    .replace(/^#{1,4} (.+)$/gm, "<b>$1</b>");
  const linhas = t.split("\n");
  let html = "", lista = null, par = [];
  const fechaPar = () => { if (par.length) { html += `<p>${par.join("<br>")}</p>`; par = []; } };
  const fechaLista = () => { if (lista) { html += `</${lista}>`; lista = null; } };
  for (const l of linhas) {
    const ul = l.match(/^\s*[-*•] (.*)/), ol = l.match(/^\s*\d+[.)] (.*)/);
    if (ul || ol) {
      fechaPar();
      const tipo = ul ? "ul" : "ol";
      if (lista !== tipo) { fechaLista(); html += `<${tipo}>`; lista = tipo; }
      html += `<li>${(ul || ol)[1]}</li>`;
    } else if (!l.trim()) { fechaPar(); fechaLista(); }
    else { fechaLista(); par.push(l); }
  }
  fechaPar(); fechaLista();
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre><code>${blocos[i]}</code></pre>`);
}

// ---------- tema ----------
function aplicarTema(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-tema");
  else document.documentElement.setAttribute("data-tema", t);
  try { localStorage.setItem("tema", t); } catch (e) {}
}
try { aplicarTema(localStorage.getItem("tema") || "auto"); } catch (e) {}
$$("[data-tema]").forEach((b) => b.addEventListener("click", () => aplicarTema(b.dataset.tema)));

// ---------- acesso ----------
function mostrarAcesso(login) {
  $("#app").classList.add("oculto");
  $("#tela-acesso").classList.remove("oculto");
  $("#form-primeiro").classList.toggle("oculto", login);
  $("#form-login").classList.toggle("oculto", !login);
  $("#acesso-erro").textContent = "";
}

async function iniciar() {
  const e = await api("/api/estado");
  est.nome = e.nome;
  if (!e.configurado) return mostrarAcesso(false);
  if (!e.logado) { $("#login-titulo").textContent = `Oi! Sou o ${e.nome} 👋`; return mostrarAcesso(true); }
  $("#tela-acesso").classList.add("oculto");
  $("#app").classList.remove("oculto");
  $("#topo-titulo").textContent = e.nome;
  $("#topo-avatar").textContent = e.nome.slice(0, 1).toUpperCase();
  document.title = e.nome;
  await abrirUltimaConversa();
  vigiarAvisos();
}

$("#pa-ok").onclick = async () => {
  const s1 = $("#pa-senha").value, s2 = $("#pa-senha2").value;
  if (s1 !== s2) return ($("#acesso-erro").textContent = "As senhas nao batem.");
  try {
    await api("/api/primeiro-acesso", { method: "POST", corpo: { senha: s1, nome_agente: $("#pa-nome").value || "Levi", nome_dono: $("#pa-dono").value } });
    iniciar();
  } catch (e) { $("#acesso-erro").textContent = e.message; }
};
async function entrar() {
  try {
    await api("/api/entrar", { method: "POST", corpo: { senha: $("#lg-senha").value } });
    $("#lg-senha").value = "";
    iniciar();
  } catch (e) { $("#acesso-erro").textContent = e.message; }
}
$("#lg-ok").onclick = entrar;
$("#lg-senha").addEventListener("keydown", (e) => e.key === "Enter" && entrar());

// ---------- navegacao ----------
function irPara(tela) {
  $$(".tela").forEach((t) => t.classList.toggle("ativa", t.id === `tela-${tela}`));
  $$("nav.abas button").forEach((b) => b.classList.toggle("ativa", b.dataset.tela === tela));
  $("#btn-nova").classList.toggle("oculto", tela !== "chat");
  $("#btn-gaveta").classList.toggle("oculto", tela !== "chat");
  ({ lembretes: carregarLembretes, cerebro: carregarCerebro, memoria: carregarMemoria, config: carregarConfig })[tela]?.();
}
$$("nav.abas button").forEach((b) => b.addEventListener("click", () => irPara(b.dataset.tela)));

// ---------- conversas ----------
async function abrirUltimaConversa() {
  const lista = await api("/api/conversas");
  if (lista.length) await abrirConversa(lista[0].id, lista[0].titulo);
  else await novaConversa();
}
async function novaConversa() {
  const c = await api("/api/conversas", { method: "POST" });
  await abrirConversa(c.id, c.titulo);
  fecharGaveta();
}
$("#btn-nova").onclick = novaConversa;
$("#gv-nova").onclick = novaConversa;

async function abrirConversa(id, titulo) {
  est.conversa = id;
  const msgs = await api(`/api/conversas/${id}/mensagens`);
  const caixa = $("#mensagens");
  caixa.innerHTML = "";
  if (!msgs.length) caixa.appendChild(telaVazia());
  msgs.forEach((m) => caixa.appendChild(bolha(m)));
  rolar();
}

function telaVazia() {
  const d = document.createElement("div");
  d.className = "vazio";
  d.innerHTML = `<div class="avatar">${esc(est.nome.slice(0, 1).toUpperCase())}</div>
    <b>Oi! Sou o ${esc(est.nome)}.</b><br>Pode falar comigo por texto, audio ou foto.
    <div class="sugestoes"></div>`;
  ["Me lembra daqui a 10 minutos de beber agua", "Pesquisa a previsao do tempo pra Votorantim", "Anota que meu aniversario e 7 de marco"]
    .forEach((s) => {
      const b = document.createElement("button");
      b.textContent = s;
      b.onclick = () => { $("#caixa").value = s; enviar(); };
      d.querySelector(".sugestoes").appendChild(b);
    });
  return d;
}

function bolha(m) {
  const d = document.createElement("div");
  d.className = `msg ${m.papel === "user" ? "eu" : "ia"}`;
  let html = "";
  if (m.imagem) html += `<img src="${esc(m.imagem)}" alt="imagem enviada">`;
  if (m.papel !== "user" && m.info?.acoes?.length) html += `<div class="acoes">${m.info.acoes.map((a) => `<div>${esc(a)}</div>`).join("")}</div>`;
  html += m.papel === "user" ? esc(m.conteudo).replace(/\n/g, "<br>") : md(m.conteudo);
  d.innerHTML = html;
  if (m.papel !== "user" && m.modelo) {
    const s = document.createElement("button");
    s.className = "selo";
    s.textContent = `🧠 ${m.modelo}`;
    s.title = "Qual IA respondeu";
    s.onclick = () => {
      const t = m.info?.tentativas || [];
      if (d.querySelector(".detalhe")) return d.querySelector(".detalhe").remove();
      const det = document.createElement("div");
      det.className = "detalhe";
      det.innerHTML = t.length ? "Antes desta, tentei:<br>" + t.map((x) => `• ${esc(x.modelo)}: ${esc(x.erro)}`).join("<br>") : "Respondeu de primeira.";
      d.appendChild(det);
    };
    d.appendChild(s);
  }
  return d;
}
const rolar = () => { const c = $("#mensagens"); c.scrollTop = c.scrollHeight; };

async function enviar() {
  if (est.enviando) return;
  const texto = $("#caixa").value.trim();
  if (!texto && !est.imagem) return;
  est.enviando = true;
  $("#btn-enviar").disabled = true;
  const imagem = est.imagem;
  $("#caixa").value = "";
  ajustarCaixa();
  tirarImagem();
  $("#mensagens .vazio")?.remove();
  $("#mensagens").appendChild(bolha({ papel: "user", conteudo: texto, imagem }));
  const dig = document.createElement("div");
  dig.className = "digitando";
  dig.innerHTML = "<span></span><span></span><span></span>";
  $("#mensagens").appendChild(dig);
  rolar();
  try {
    const r = await api(`/api/conversas/${est.conversa}/enviar`, { method: "POST", corpo: { texto, imagem } });
    dig.replaceWith(bolha({ papel: "assistant", ...r }));
  } catch (e) {
    dig.replaceWith(bolha({ papel: "assistant", conteudo: `Ops: ${e.message}` }));
  } finally {
    est.enviando = false;
    $("#btn-enviar").disabled = false;
    rolar();
  }
}
$("#btn-enviar").onclick = enviar;
function ajustarCaixa() { const c = $("#caixa"); c.style.height = "auto"; c.style.height = Math.min(c.scrollHeight, 140) + "px"; }
$("#caixa").addEventListener("input", ajustarCaixa);
$("#caixa").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && window.matchMedia("(pointer: fine)").matches) { e.preventDefault(); enviar(); }
});

// gaveta
async function abrirGaveta() {
  const lista = await api("/api/conversas");
  const box = $("#lista-conversas");
  box.innerHTML = "";
  lista.forEach((c) => {
    const d = document.createElement("div");
    d.className = `item-conversa${c.id === est.conversa ? " ativa" : ""}`;
    d.innerHTML = `<button class="abrir"></button><button class="btn-icone" aria-label="Apagar">🗑️</button>`;
    d.querySelector(".abrir").textContent = c.titulo;
    d.querySelector(".abrir").onclick = () => { abrirConversa(c.id, c.titulo); fecharGaveta(); };
    d.querySelector(".btn-icone").onclick = async () => {
      if (!confirm(`Apagar "${c.titulo}"?`)) return;
      await api(`/api/conversas/${c.id}`, { method: "DELETE" });
      if (c.id === est.conversa) await abrirUltimaConversa();
      abrirGaveta();
    };
    box.appendChild(d);
  });
  $("#gaveta").classList.remove("oculto");
}
const fecharGaveta = () => $("#gaveta").classList.add("oculto");
$("#btn-gaveta").onclick = abrirGaveta;
$("#gaveta .fundo-escuro").onclick = fecharGaveta;

// ---------- imagem ----------
$("#btn-anexo").onclick = () => $("#arq-img").click();
$("#arq-img").onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  est.imagem = await reduzir(f, 1280);
  $("#previa img").src = est.imagem;
  $("#previa").classList.remove("oculto");
};
function tirarImagem() { est.imagem = null; $("#previa").classList.add("oculto"); }
$("#tirar-img").onclick = tirarImagem;
function reduzir(arquivo, max) {
  return new Promise((ok, falha) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src);
      ok(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = falha;
    img.src = URL.createObjectURL(arquivo);
  });
}

// ---------- audio ----------
$("#btn-mic").onclick = async () => {
  if (est.gravador) return est.gravador.stop();
  if (!navigator.mediaDevices?.getUserMedia) return avisar("O microfone so funciona no endereco https (Tailscale). Veja o README.", 6000);
  try {
    const fluxo = await navigator.mediaDevices.getUserMedia({ audio: true });
    const partes = [];
    const g = new MediaRecorder(fluxo);
    g.ondataavailable = (e) => partes.push(e.data);
    g.onstop = async () => {
      fluxo.getTracks().forEach((t) => t.stop());
      est.gravador = null;
      $("#btn-mic").classList.remove("gravando");
      const blob = new Blob(partes, { type: g.mimeType || "audio/webm" });
      const fd = new FormData();
      fd.append("arquivo", blob, "audio." + (blob.type.includes("mp4") ? "m4a" : "webm"));
      avisar("Transcrevendo...");
      try {
        const r = await fetch("/api/audio", { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        $("#caixa").value = d.texto;
        ajustarCaixa();
        if (d.texto) enviar();
      } catch (e) { avisar(`Audio: ${e.message}`); }
    };
    g.start();
    est.gravador = g;
    $("#btn-mic").classList.add("gravando");
    avisar("Gravando... toque de novo pra parar");
  } catch (e) { avisar("Nao consegui usar o microfone."); }
};

// ---------- lembretes ----------
async function carregarLembretes() {
  const lista = await api("/api/lembretes");
  const box = $("#lista-lembretes");
  box.innerHTML = lista.length ? "" : '<p class="ajuda">Nenhum lembrete marcado.</p>';
  const rep = { diario: "todo dia", semanal: "toda semana" };
  lista.forEach((l) => {
    const d = document.createElement("div");
    d.className = "lembrete";
    const quando = new Date(l.quando * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    d.innerHTML = `<div class="meio"><div></div><small>${esc(quando)} ${rep[l.repetir] ? "· " + rep[l.repetir] : ""} ${l.tarefa_ia ? "· IA faz" : ""}</small></div><button class="btn-icone" aria-label="Apagar">🗑️</button>`;
    d.querySelector(".meio div").textContent = l.texto;
    d.querySelector("button").onclick = async () => { await api(`/api/lembretes/${l.id}`, { method: "DELETE" }); carregarLembretes(); };
    box.appendChild(d);
  });
  atualizarPermissao();
}
$("#lb-criar").onclick = async () => {
  try {
    const r = await api("/api/lembretes", { method: "POST", corpo: { texto: $("#lb-texto").value, quando: $("#lb-quando").value, repetir: $("#lb-repetir").value, tarefa_ia: $("#lb-ia").checked } });
    avisar(r.msg);
    $("#lb-texto").value = "";
    carregarLembretes();
  } catch (e) { avisar(e.message); }
};
function atualizarPermissao() {
  const s = $("#permissao-status");
  if (!("Notification" in window)) return (s.textContent = "Este navegador nao tem avisos (no iPhone, instale o app na tela inicial).");
  s.textContent = Notification.permission === "granted" ? " Ligados ✓" : "";
}
$("#btn-permissao").onclick = async () => {
  if (!("Notification" in window)) return avisar("Avisos precisam do endereco https (Tailscale).");
  await Notification.requestPermission();
  atualizarPermissao();
};

async function vigiarAvisos() {
  clearTimeout(vigiarAvisos.t);
  try {
    const novos = await api(`/api/notificacoes?depois=${est.ultimoAviso}`);
    for (const n of novos) {
      est.ultimoAviso = Math.max(est.ultimoAviso, n.id);
      avisar(`⏰ ${n.texto}`, 9000);
      if ("Notification" in window && Notification.permission === "granted") {
        const reg = await navigator.serviceWorker?.getRegistration();
        const op = { body: n.texto, icon: "/icones/icone-192.png", tag: `aviso-${n.id}` };
        if (reg) reg.showNotification(n.titulo, op); else new Notification(n.titulo, op);
      }
      navigator.vibrate?.([200, 100, 200]);
    }
    if (novos.length) {
      await api("/api/notificacoes/lidas", { method: "POST" });
      if (novos.some((n) => n.titulo === "Tarefa feita") && est.conversa) abrirUltimaConversa();
    }
  } catch (e) {}
  vigiarAvisos.t = setTimeout(vigiarAvisos, document.hidden ? 60000 : 20000);
}
document.addEventListener("visibilitychange", () => !document.hidden && est.conversa && vigiarAvisos());

// ---------- cerebro ----------
let combo = [], provedores = {};
async function carregarCerebro() {
  const d = await api("/api/cerebro");
  combo = d.combo; provedores = d.provedores;
  const sel = $("#cb-prov");
  sel.innerHTML = Object.entries(provedores).map(([k, p]) => `<option value="${k}">${esc(p.nome)}${p.so_free ? " (so :free)" : ""}</option>`).join("");
  desenharCombo();
}
function desenharCombo() {
  const box = $("#lista-combo");
  box.innerHTML = "";
  combo.forEach((e, i) => {
    const d = document.createElement("div");
    const ligado = e.ligado !== false;
    d.className = `entrada${ligado ? "" : " desligada"}`;
    let chip = '<span class="chip">pronta</span>';
    if (!e.tem_chave) chip = '<span class="chip cinza">sem chave</span>';
    else if (e.pausado_ate) chip = `<span class="chip laranja">${esc(e.motivo)} · volta ${new Date(e.pausado_ate * 1000).toLocaleTimeString("pt-BR", { timeStyle: "short" })}</span>`;
    d.innerHTML = `<div class="num">${i + 1}</div>
      <div class="meio"><b></b><small>${esc(e.nome_provedor || provedores[e.provedor]?.nome || e.provedor)}${e.visao ? " · 👁️ imagens" : ""}${e.ferramentas === false ? " · ferramentas por texto" : ""}</small><div>${chip}</div></div>
      <div class="botoes"><button data-a="sobe" aria-label="Subir">▲</button><button data-a="desce" aria-label="Descer">▼</button><button data-a="liga" aria-label="Ligar ou desligar">${ligado ? "⏸" : "▶"}</button><button data-a="tira" aria-label="Tirar">✕</button></div>`;
    d.querySelector("b").textContent = e.modelo;
    d.querySelectorAll("[data-a]").forEach((b) => (b.onclick = () => mexer(i, b.dataset.a)));
    box.appendChild(d);
  });
}
async function mexer(i, acao) {
  if (acao === "sobe" && i > 0) [combo[i - 1], combo[i]] = [combo[i], combo[i - 1]];
  if (acao === "desce" && i < combo.length - 1) [combo[i + 1], combo[i]] = [combo[i], combo[i + 1]];
  if (acao === "liga") combo[i].ligado = combo[i].ligado === false;
  if (acao === "tira") { if (!confirm(`Tirar ${combo[i].modelo} do combo?`)) return; combo.splice(i, 1); }
  await api("/api/cerebro", { method: "PUT", corpo: { entradas: combo } });
  carregarCerebro();
}
$("#cb-buscar").onclick = async () => {
  try {
    avisar("Buscando...");
    const d = await api(`/api/cerebro/modelos/${$("#cb-prov").value}`);
    $("#cb-lista").innerHTML = d.modelos.map((m) => `<option value="${esc(m)}">`).join("");
    avisar(`${d.modelos.length} modelos. Toque no campo Modelo pra escolher.`);
  } catch (e) { avisar(e.message); }
};
$("#cb-add").onclick = async () => {
  const modelo = $("#cb-modelo").value.trim();
  const provedor = $("#cb-prov").value;
  if (!modelo) return avisar("Escolha um modelo.");
  if (provedores[provedor]?.so_free && !modelo.endsWith(":free")) return avisar("No OpenRouter so vale modelo que termina em :free (gratis).");
  combo.push({ provedor, modelo, visao: $("#cb-visao").checked, ferramentas: $("#cb-tools").checked, ligado: true });
  await api("/api/cerebro", { method: "PUT", corpo: { entradas: combo } });
  $("#cb-modelo").value = "";
  avisar("Adicionado no fim do combo.");
  carregarCerebro();
};
$("#cb-despausar").onclick = async () => { await api("/api/cerebro/despausar", { method: "POST" }); avisar("Pronto, vou tentar todas de novo."); carregarCerebro(); };
$("#cb-padrao").onclick = async () => { if (!confirm("Voltar o combo ao padrao?")) return; await api("/api/cerebro/padrao", { method: "POST" }); carregarCerebro(); };

// ---------- memoria ----------
async function carregarMemoria() {
  const d = await api("/api/memoria");
  $("#mm-persona").value = d.persona;
  $("#mm-memoria").value = d.memoria;
  const sel = $("#mm-dias");
  sel.innerHTML = d.dias.length ? d.dias.map((x) => `<option>${esc(x)}</option>`).join("") : "<option value=''>Ainda sem diario</option>";
  verDiario();
}
async function verDiario() {
  const dia = $("#mm-dias").value;
  $("#mm-diario").textContent = dia ? (await api(`/api/diario/${dia}`)).texto : "";
}
$("#mm-dias").onchange = verDiario;
async function salvarMem(campo, st) {
  try { await api("/api/memoria", { method: "PUT", corpo: { [campo]: $(`#mm-${campo}`).value } }); $(st).textContent = "Salvo ✓"; $(st).className = "status bom"; }
  catch (e) { $(st).textContent = e.message; $(st).className = "status ruim"; }
  setTimeout(() => ($(st).textContent = ""), 3000);
}
$("#mm-salvar-persona").onclick = () => salvarMem("persona", "#mm-st1");
$("#mm-salvar-memoria").onclick = () => salvarMem("memoria", "#mm-st2");

// ---------- configuracao ----------
async function carregarConfig() {
  carregarCerebroNotas().catch(() => {});
  const d = await api("/api/config");
  const box = $("#lista-chaves");
  box.innerHTML = "";
  d.chaves.forEach((c) => {
    const div = document.createElement("div");
    div.style.marginBottom = "18px";
    div.innerHTML = `<label>${esc(c.rotulo)} ${c.configurada ? '<span class="chip">guardada ✓</span>' : '<span class="chip cinza">vazia</span>'}</label>
      <input type="password" autocomplete="off" placeholder="${c.configurada ? "•••••••• (cole outra pra trocar)" : "cole a chave aqui"}">
      <div class="fila" style="margin-top:8px">
        <button class="btn" data-a="salvar">Salvar</button>
        <button class="btn claro" data-a="testar">Testar</button>
        ${c.configurada ? '<button class="btn perigo" data-a="apagar">Apagar</button>' : ""}
        <a href="${esc(c.site)}" target="_blank" rel="noopener" style="font-size:14px">Pegar chave gratis</a>
      </div><div class="status"></div>`;
    const inp = div.querySelector("input"), st = div.querySelector(".status");
    div.querySelector("[data-a=salvar]").onclick = async () => {
      if (!inp.value.trim()) return avisar("Cole a chave primeiro.");
      await api("/api/config/chave", { method: "PUT", corpo: { nome: c.nome, valor: inp.value } });
      avisar("Chave guardada ✓");
      carregarConfig();
    };
    div.querySelector("[data-a=testar]").onclick = async () => {
      st.textContent = "Testando..."; st.className = "status";
      const r = await api(`/api/config/testar/${c.provedor}`, { method: "POST" });
      st.textContent = r.ok ? "✓ " + r.msg : "✕ " + r.msg;
      st.className = `status ${r.ok ? "bom" : "ruim"}`;
    };
    const ap = div.querySelector("[data-a=apagar]");
    if (ap) ap.onclick = async () => {
      if (!confirm(`Apagar a chave ${c.rotulo}?`)) return;
      await api("/api/config/chave", { method: "PUT", corpo: { nome: c.nome, valor: "" } });
      carregarConfig();
    };
    box.appendChild(div);
  });
}
// ---------- segundo cerebro ----------
function mostrarCerebro(d) {
  $("#cc-token-chip").innerHTML = d.tem_token ? '<span class="chip">guardado ✓</span>' : '<span class="chip cinza">vazio</span>';
  $("#cc-repo").value = d.repo || "";
  $("#cc-excluidos").value = (d.excluidos || []).join("\n");
  const s = d.sync, st = $("#cc-status");
  if (!s) { st.textContent = "Ainda nao sincronizado."; st.className = "status"; }
  else {
    const q = new Date(s.quando * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    st.textContent = `${s.ok ? "✓" : "✕"} Ultima sincronizacao: ${q} · ${s.msg}`;
    st.className = `status ${s.ok ? "bom" : "ruim"}`;
  }
  $("#cc-pastas").textContent = d.total ? `${d.total} notas. Pastas: ${d.pastas.join(" ")}` : "";
}
async function carregarCerebroNotas() { mostrarCerebro(await api("/api/conhecimento")); }
$("#cc-salvar-token").onclick = async () => {
  const v = $("#cc-token").value.trim();
  if (!v) return avisar("Cole o token primeiro.");
  await api("/api/config/chave", { method: "PUT", corpo: { nome: "CEREBRO_GITHUB_TOKEN", valor: v } });
  $("#cc-token").value = "";
  avisar("Token guardado ✓ Agora toque em Sincronizar.");
  carregarCerebroNotas();
};
$("#cc-salvar").onclick = async () => {
  try {
    const excluidos = $("#cc-excluidos").value.split("\n").map((x) => x.trim()).filter(Boolean);
    mostrarCerebro(await api("/api/conhecimento", { method: "PUT", corpo: { repo: $("#cc-repo").value, excluidos } }));
    avisar("Salvo ✓");
  } catch (e) { avisar(e.message); }
};
$("#cc-sync").onclick = async () => {
  const b = $("#cc-sync");
  b.disabled = true; b.textContent = "Sincronizando...";
  try { const r = await api("/api/conhecimento/sincronizar", { method: "POST" }); mostrarCerebro(r); avisar(r.ok ? "✓ " + r.msg : "✕ " + r.msg, 6000); }
  catch (e) { avisar(e.message); }
  finally { b.disabled = false; b.textContent = "Sincronizar agora"; }
};

$("#sn-ok").onclick = async () => {
  const st = $("#sn-st");
  try {
    await api("/api/senha", { method: "POST", corpo: { atual: $("#sn-atual").value, nova: $("#sn-nova").value } });
    st.textContent = "Senha trocada ✓"; st.className = "status bom";
    $("#sn-atual").value = $("#sn-nova").value = "";
  } catch (e) { st.textContent = e.message; st.className = "status ruim"; }
};
$("#btn-sair").onclick = async () => { await api("/api/sair", { method: "POST" }); location.reload(); };

// ---------- PWA ----------
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
iniciar().catch((e) => avisar(e.message));
