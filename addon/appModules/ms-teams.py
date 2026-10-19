# Teams Chat: módulo de aplicación para el nuevo Microsoft Teams (ms-teams.exe).
# Solo se carga cuando Teams está activo, así que no afecta al resto de NVDA.

import os
import re
import time
from collections import namedtuple
from functools import partial

import api
import appModuleHandler
import config
import gui
import ui
import wx
from controlTypes import Role, State
from gui import guiHelper
from keyboardHandler import KeyboardInputGesture
from logHandler import log

# Mantener igual que en globalPlugins/teamsChat.py.
CONF_SECTION = "teamsChat"
config.conf.spec[CONF_SECTION] = {
	"numpadGestures": "boolean(default=True)",
	"attachmentAction": "option('viewer', 'browser', 'download', default='browser')",
}

# Identificadores del DOM de Teams, obtenidos explorando la interfaz real.
CHAT_LIST_ID = "chat-pane-list"
# Contenedor común de la lista de mensajes y del cuadro de redacción.
MESSAGE_PANE_ID = "message-pane-layout-a11y"
BODY_ID_PREFIX = "message-body-"
AUTHOR_ID_PREFIX = "author-"
CONTENT_ID_PREFIX = "content-"
ATTACHMENTS_ID_PREFIX = "attachments-"
MY_BODY_CLASS = "fui-ChatMyMessage__body"

# Límites para no recorrer árboles enormes si Teams cambia su estructura.
MAX_ANCESTOR_DEPTH = 60
MAX_FALLBACK_NODES = 4000
MAX_PANE_NODES = 1500
MAX_TEXT_DEPTH = 15
MAX_MY_NAME_SCAN = 30

# Segundos entre pulsaciones para contar como doble pulsación.
REPEAT_WINDOW = 0.5

# Esperas al manejar menús de Teams (milisegundos).
FOCUS_DELAY_MS = 150
MENU_POLL_MS = 100
MENU_POLL_ATTEMPTS = 20
# Texto de la opción del menú contextual de un adjunto (Teams en español).
DOWNLOAD_ITEM_NAME = "Descargar"

SCRIPT_CATEGORY = "Teams Chat"


def _attrs(obj):
	try:
		return obj.IA2Attributes or {}
	except Exception:
		return {}


def _domId(obj):
	return _attrs(obj).get("id", "")


def _children(obj):
	try:
		return obj.children
	except Exception:
		return []


def _role(obj):
	try:
		return obj.role
	except Exception:
		return None


def _name(obj):
	try:
		return obj.name or ""
	except Exception:
		return ""


def _findDescendant(obj, predicate, maxDepth):
	"""Busca en profundidad el primer descendiente que cumpla predicate."""
	stack = [(child, 1) for child in reversed(_children(obj))]
	while stack:
		node, depth = stack.pop()
		if predicate(node):
			return node
		if depth < maxDepth:
			stack.extend((child, depth + 1) for child in reversed(_children(node)))
	return None


def _findById(root, domId, maxNodes):
	"""Búsqueda en anchura de un id, con un límite de nodos visitados."""
	queue = [root]
	visited = 0
	while queue and visited < maxNodes:
		node = queue.pop(0)
		visited += 1
		if _domId(node) == domId:
			return node
		queue.extend(_children(node))
	return None


def _collectText(obj, depth=0):
	"""Junta el texto visible de un objeto: los nombres de sus hojas."""
	kids = _children(obj)
	if not kids or depth >= MAX_TEXT_DEPTH:
		try:
			name = obj.name
		except Exception:
			name = None
		return [name] if name else []
	parts = []
	for kid in kids:
		parts.extend(_collectText(kid, depth + 1))
	return parts


def _cleanText(parts):
	return re.sub(r"\s+", " ", " ".join(parts)).strip()


URL_RE = re.compile(r"https?://\S+")

# obj es el elemento del adjunto en Teams (para abrirlo en el visor).
Attachment = namedtuple("Attachment", "name url obj")


class ChatMessage:
	def __init__(
		self, container, body, author, text, isMine,
		quoteAuthor=None, quoteText=None, links=(), attachments=(),
	):
		self.container = container
		self.body = body
		self.author = author
		self.text = text
		self.isMine = isMine
		self.quoteAuthor = quoteAuthor
		self.quoteText = quoteText
		# Listas de (texto o nombre de archivo, dirección).
		self.links = list(links)
		self.attachments = list(attachments)

	def speechText(self, myName):
		author = "Tú" if self.isMine else (self.author or "Desconocido")
		parts = [self.text] if self.text else []
		if self.attachments:
			names = ", ".join(att.name for att in self.attachments)
			label = "Adjunto" if len(self.attachments) == 1 else f"{len(self.attachments)} adjuntos"
			parts.append(f"{label}: {names}")
		speech = f"{author}: {'. '.join(parts) or 'mensaje sin texto'}"
		if self.quoteText is not None:
			if myName and self.quoteAuthor == myName:
				speech += f". Mensaje citado tuyo: {self.quoteText}"
			elif self.quoteAuthor:
				speech += f". Mensaje citado de {self.quoteAuthor}: {self.quoteText}"
			else:
				speech += f". Mensaje citado: {self.quoteText}"
		return speech


def _isQuote(obj):
	"""Una cita es un grupo cuyo primer hijo es el autor citado y el último el texto citado."""
	if _role(obj) != Role.GROUPING:
		return False
	kids = _children(obj)
	return len(kids) >= 3 and _role(kids[0]) == Role.STATICTEXT and _role(kids[-1]) != Role.STATICTEXT


def _parseMessage(container, body):
	msgId = _domId(body)[len(BODY_ID_PREFIX):]
	isMine = MY_BODY_CLASS in _attrs(body).get("class", "")
	authorObj = _findDescendant(container, lambda o: _domId(o) == AUTHOR_ID_PREFIX + msgId, 4)
	author = _cleanText(_collectText(authorObj)) if authorObj else ""
	content = _findDescendant(body, lambda o: _domId(o) == CONTENT_ID_PREFIX + msgId, 2)
	quoteAuthor = quoteText = None
	textParts = []
	if content:
		for part in _children(content):
			if quoteText is None and _isQuote(part):
				quoteKids = _children(part)
				quoteAuthor = _cleanText(_collectText(quoteKids[0]))
				quoteText = _cleanText(_collectText(quoteKids[-1]))
			else:
				textParts.extend(_collectText(part))
	return ChatMessage(
		container, body, author, _cleanText(textParts), isMine, quoteAuthor, quoteText,
		_parseLinks(content) if content else [],
		_parseAttachments(body, msgId),
	)


def _parseLinks(content):
	"""Enlaces dentro del texto del mensaje; su valor es la dirección."""
	links = []
	stack = [(child, 1) for child in reversed(_children(content))]
	while stack:
		node, depth = stack.pop()
		if _role(node) == Role.LINK:
			try:
				url = node.value
			except Exception:
				url = None
			if url:
				links.append((_cleanText(_collectText(node)) or url, url))
			continue
		if depth < MAX_TEXT_DEPTH:
			stack.extend((child, depth + 1) for child in reversed(_children(node)))
	return links


def _parseAttachments(body, msgId):
	"""Archivos adjuntos: cada uno es un grupo cuyo nombre es el nombre del archivo.
	La dirección aparece dentro, en el nombre de una sección "<archivo> <dirección>".
	"""
	box = next((c for c in _children(body) if _domId(c) == ATTACHMENTS_ID_PREFIX + msgId), None)
	if box is None:
		return []
	attachments = []
	for item in _children(box):
		name = _name(item)
		if not name:
			continue
		withUrl = _findDescendant(item, lambda o: bool(URL_RE.search(_name(o))), 6)
		match = URL_RE.search(_name(withUrl)) if withUrl else None
		attachments.append(Attachment(name, match.group(0) if match else None, item))
	return attachments


def _openUrl(url):
	# Solo direcciones web: nunca ejecutar otra cosa que venga del chat.
	if not URL_RE.match(url or ""):
		ui.message("Dirección no válida")
		return
	os.startfile(url)


def _sendKey(name):
	"""Envía una tecla directamente a Teams, sin pasar por los atajos de NVDA."""
	KeyboardInputGesture.fromName(name).send()


class MessageActionsDialog(wx.Dialog):
	def __init__(self, parent, summary, labels):
		super().__init__(parent, title="Acciones del mensaje")
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
		self.actionsList = helper.addLabeledControl(summary, wx.ListBox, choices=labels)
		self.actionsList.SetSelection(0)
		self.actionsList.Bind(wx.EVT_LISTBOX_DCLICK, lambda evt: self.EndModal(wx.ID_OK))
		helper.addDialogDismissButtons(self.CreateButtonSizer(wx.OK | wx.CANCEL))
		mainSizer.Add(helper.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL)
		self.SetSizer(mainSizer)
		mainSizer.Fit(self)
		self.actionsList.SetFocus()
		self.CentreOnScreen()


def _iterMessages(chatList):
	"""Recorre los mensajes de abajo hacia arriba, saltando encabezados y separadores."""
	child = chatList.lastChild
	while child:
		if _role(child) != Role.HEADING:
			body = _findDescendant(child, lambda o: _domId(o).startswith(BODY_ID_PREFIX), 3)
			if body:
				yield child, body
		child = child.previous


class AppModule(appModuleHandler.AppModule):

	__gestures = {
		**{f"kb:alt+{i}": f"readMessage{i}" for i in range(10)},
		**{f"kb:alt+numLockNumpad{i}": f"readMessage{i}" for i in range(10)},
		**{f"kb:NVDA+alt+{i}": f"messageActions{i}" for i in range(10)},
		**{f"kb:NVDA+alt+numLockNumpad{i}": f"messageActions{i}" for i in range(10)},
		"kb:NVDA+alt+e": "focusComposeBox",
	}

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._myName = None
		# Última lista encontrada y el título del documento en ese momento.
		# Al cambiar de chat cambia el título y se vuelve a buscar.
		self._cachedChatList = None
		self._cachedDocName = None
		# Última pulsación: (índice, momento en que terminó, nº de pulsaciones, mensaje).
		self._lastPress = None

	def getScript(self, gesture):
		script = super().getScript(gesture)
		if (
			script is not None
			and getattr(script, "teamsChatScript", False)
			and getattr(gesture, "mainKeyName", "").startswith("numLockNumpad")
			and not config.conf[CONF_SECTION]["numpadGestures"]
		):
			# Teclado numérico desactivado: la tecla sigue su camino normal (códigos Alt).
			return None
		return script

	def _currentDocName(self):
		"""Título del documento de Teams (cambia al cambiar de chat)."""
		obj = api.getFocusObject()
		for _ in range(MAX_ANCESTOR_DEPTH):
			if obj is None:
				return None
			if _role(obj) == Role.DOCUMENT:
				return _name(obj) or None
			obj = obj.parent
		return None

	def _findChatList(self):
		"""Devuelve (lista, método usado) o (None, None)."""
		pane = None
		docName = None
		for start in (api.getFocusObject(), api.getNavigatorObject()):
			obj = start
			for _ in range(MAX_ANCESTOR_DEPTH):
				if obj is None:
					break
				domId = _domId(obj)
				if domId == CHAT_LIST_ID:
					return self._remember(obj), "ancestro"
				if domId == MESSAGE_PANE_ID and pane is None:
					pane = obj
				# De paso, el título del documento para validar la caché.
				if docName is None and _role(obj) == Role.DOCUMENT:
					docName = _name(obj) or None
				obj = obj.parent
		cached = self._cachedChatList
		if cached is not None and docName and docName == self._cachedDocName and _domId(cached) == CHAT_LIST_ID:
			return cached, "caché"
		if pane is not None:
			found = _findById(pane, CHAT_LIST_ID, MAX_PANE_NODES)
			if found:
				return self._remember(found, docName), "panel"
		found = self._findChatListInBuffer()
		if found:
			return self._remember(found, docName), "búfer"
		found = self._findChatListByWalking()
		if found:
			return self._remember(found, docName), "recorrido"
		return None, None

	def _remember(self, chatList, docName=None):
		self._cachedChatList = chatList
		self._cachedDocName = docName if docName is not None else self._currentDocName()
		return chatList

	def _findChatListInBuffer(self):
		"""Búsqueda rápida en el búfer de modo exploración de NVDA."""
		ti = getattr(api.getFocusObject(), "treeInterceptor", None)
		if ti is None or not hasattr(ti, "_iterNodesByAttribs"):
			return None
		try:
			for item in ti._iterNodesByAttribs({"IAccessible2::attribute_id": [CHAT_LIST_ID]}):
				obj = item.obj
				if obj is not None and _domId(obj) == CHAT_LIST_ID:
					return obj
		except Exception:
			log.debugWarning("teamsChat: búsqueda en el búfer fallida", exc_info=True)
		return None

	def _findChatListByWalking(self):
		"""Último recurso: recorrer el documento con un límite de nodos."""
		doc = api.getFocusObject()
		for _ in range(MAX_ANCESTOR_DEPTH):
			if doc is None or _role(doc) == Role.DOCUMENT:
				break
			doc = doc.parent
		if doc is None:
			return None
		return _findById(doc, CHAT_LIST_ID, MAX_FALLBACK_NODES)

	def _getMessage(self, chatList, index):
		count = 0
		for container, body in _iterMessages(chatList):
			if count == index:
				return _parseMessage(container, body), count
			count += 1
		return None, count

	def _getMyName(self, chatList):
		if self._myName:
			return self._myName
		for scanned, (container, body) in enumerate(_iterMessages(chatList)):
			if scanned >= MAX_MY_NAME_SCAN:
				break
			if MY_BODY_CLASS in _attrs(body).get("class", ""):
				msg = _parseMessage(container, body)
				if msg.author:
					self._myName = msg.author
					break
		return self._myName

	def _locateMessage(self, index):
		"""Devuelve el mensaje pedido o None tras avisar al usuario."""
		start = time.perf_counter()
		chatList, method = self._findChatList()
		found = time.perf_counter()
		if chatList is None:
			log.info(f"teamsChat: lista no encontrada en {(found - start) * 1000:.0f} ms")
			ui.message("No se encontró la lista de mensajes. Abre un chat.")
			return None, None
		msg, count = self._getMessage(chatList, index)
		log.info(
			f"teamsChat: mensaje {index}, buscar lista {(found - start) * 1000:.0f} ms ({method}), "
			f"leer mensaje {(time.perf_counter() - found) * 1000:.0f} ms"
		)
		if msg is None:
			ui.message(f"Solo hay {count} mensajes cargados" if count else "No hay mensajes cargados")
		return msg, chatList

	def _readMessage(self, index):
		# Contamos las pulsaciones nosotros, desde que terminó la anterior:
		# el contador de NVDA mide desde que empezó, y si la lectura tarda
		# la segunda pulsación ya no cuenta como repetición.
		last = self._lastPress
		isRepeat = (
			last is not None
			and last[0] == index
			and time.perf_counter() - last[1] < REPEAT_WINDOW
		)
		presses = last[2] + 1 if isRepeat else 1
		if presses == 2 and last[3] is not None:
			msg = last[3]
			# Sin texto (solo adjuntos): copiar las direcciones de los adjuntos.
			toCopy = msg.text or "\n".join(att.url for att in msg.attachments if att.url)
			if toCopy:
				api.copyToClip(toCopy)
				ui.message("Copiado")
			else:
				ui.message("No hay texto que copiar")
		else:
			# Tercera pulsación o más: vuelve a empezar leyendo.
			presses = 1
			msg, chatList = self._locateMessage(index)
			if msg is not None:
				if msg.isMine:
					self._myName = msg.author or self._myName
				myName = self._getMyName(chatList) if msg.quoteText is not None else self._myName
				ui.message(msg.speechText(myName))
		self._lastPress = (index, time.perf_counter(), presses, msg)

	def _showMessageActions(self, index):
		msg, chatList = self._locateMessage(index)
		if msg is None:
			return
		myName = self._getMyName(chatList) if msg.quoteText is not None else self._myName
		actions = self._buildActions(msg)
		wx.CallAfter(self._runActionsDialog, msg.speechText(myName), actions)

	def _buildActions(self, msg):
		"""Lista de (etiqueta, función). Primero lo más probable:
		la acción por defecto de cada adjunto y abrir cada enlace."""
		default = config.conf[CONF_SECTION]["attachmentAction"]
		first, middle, rest = [], [], []
		for att in msg.attachments:
			options = [
				("viewer", "abrir en el visor de Teams", partial(self._openInViewer, att)),
				("download", "descargar", partial(self._downloadAttachment, att)),
			]
			if att.url:
				options.append(("browser", "abrir en el navegador", partial(_openUrl, att.url)))
			for key, label, func in options:
				(first if key == default else rest).append((f"Adjunto: {att.name} - {label}", func))
			if att.url:
				rest.append((
					f"Adjunto: {att.name} - copiar dirección",
					partial(self._copy, att.url, "Dirección copiada"),
				))
		for text, url in msg.links:
			first.append((f"Enlace: {text} - abrir", partial(_openUrl, url)))
			rest.append((f"Enlace: {text} - copiar dirección", partial(self._copy, url, "Dirección copiada")))
		if msg.text:
			middle.append(("Copiar texto", partial(self._copy, msg.text, "Copiado")))
		middle.append(("Abrir menú de opciones de Teams", partial(self._openMessageMenu, msg)))
		return first + middle + rest

	def _runActionsDialog(self, summary, actions):
		gui.mainFrame.prePopup()
		dialog = MessageActionsDialog(gui.mainFrame, summary, [label for label, _func in actions])
		result = dialog.ShowModal()
		selection = dialog.actionsList.GetSelection()
		dialog.Destroy()
		gui.mainFrame.postPopup()
		if result == wx.ID_OK and selection != wx.NOT_FOUND:
			# Dar tiempo a que el foco vuelva a Teams antes de actuar sobre él.
			wx.CallLater(300, self._runAction, *actions[selection])

	def _runAction(self, label, func):
		log.info(f"teamsChat: acción {label!r}")
		try:
			func()
		except Exception:
			log.error(f"teamsChat: falló la acción {label!r}", exc_info=True)
			ui.message("No se pudo completar la acción")

	def _copy(self, text, confirmation):
		api.copyToClip(text)
		ui.message(confirmation)

	def _openInViewer(self, att):
		# Igual que a mano: el adjunto se puede enfocar y su descripción dice
		# "Presione Entrar para abrir el archivo".
		att.obj.setFocus()
		wx.CallLater(FOCUS_DELAY_MS, _sendKey, "enter")

	def _downloadAttachment(self, att):
		# Igual que a mano: menú contextual del adjunto y opción "Descargar",
		# que Teams guarda directamente en la carpeta de descargas.
		att.obj.setFocus()
		wx.CallLater(FOCUS_DELAY_MS, _sendKey, "shift+f10")
		wx.CallLater(FOCUS_DELAY_MS + MENU_POLL_MS, self._clickMenuItem, DOWNLOAD_ITEM_NAME, MENU_POLL_ATTEMPTS)

	def _clickMenuItem(self, wanted, attemptsLeft):
		"""Espera a que se abra el menú y pulsa la opción cuyo nombre empieza por wanted."""
		focus = api.getFocusObject()
		if _role(focus) != Role.MENUITEM:
			if attemptsLeft > 0:
				wx.CallLater(MENU_POLL_MS, self._clickMenuItem, wanted, attemptsLeft - 1)
			else:
				log.info(f"teamsChat: no se abrió el menú; foco en {_role(focus)} {_name(focus)!r}")
				ui.message("No se abrió el menú del adjunto")
			return
		menu = focus.parent
		item = _findDescendant(
			menu,
			lambda o: _role(o) == Role.MENUITEM and _name(o).strip().lower().startswith(wanted.lower()),
			3,
		)
		if item is None:
			names = [_name(o) for o in _children(menu)]
			log.info(f"teamsChat: opción {wanted!r} no encontrada; menú: {names!r}")
			ui.message(f"No se encontró la opción {wanted}. Elígela en el menú.")
			return
		log.info(f"teamsChat: pulsando opción de menú {_name(item)!r}")
		try:
			item.doAction()
		except Exception:
			log.info("teamsChat: doAction falló, uso Intro", exc_info=True)
			item.setFocus()
			wx.CallLater(FOCUS_DELAY_MS, _sendKey, "enter")

	def _focusComposeBox(self):
		box = self._findComposeBox()
		if box is None:
			ui.message("No se encontró el cuadro de redacción. Abre un chat.")
			return
		box.setFocus()

	def _findComposeBox(self):
		"""El cuadro de redacción es el campo editable multilínea del panel del chat,
		fuera de la lista de mensajes."""
		pane = self._findMessagePane()
		if pane is None:
			log.info("teamsChat: panel del chat no encontrado")
			return None
		queue = [pane]
		visited = 0
		while queue and visited < MAX_PANE_NODES:
			node = queue.pop(0)
			visited += 1
			if _domId(node) == CHAT_LIST_ID:
				continue  # No recorrer los mensajes.
			try:
				states = node.states
			except Exception:
				states = set()
			if State.EDITABLE in states and State.MULTILINE in states:
				return node
			queue.extend(_children(node))
		log.info(f"teamsChat: cuadro de redacción no encontrado tras {visited} nodos")
		return None

	def _findMessagePane(self):
		"""Contenedor común de la lista de mensajes y el cuadro de redacción."""
		starts = [api.getFocusObject()]
		chatList, _method = self._findChatList()
		if chatList is not None:
			starts.append(chatList)
		for obj in starts:
			for _ in range(MAX_ANCESTOR_DEPTH):
				if obj is None:
					break
				if _domId(obj) == MESSAGE_PANE_ID:
					return obj
				obj = obj.parent
		return None

	def _openMessageMenu(self, msg):
		# Enfocar antes el mensaje: Teams solo muestra su barra de opciones
		# cuando el mensaje tiene el foco o el ratón encima.
		try:
			msg.body.setFocus()
		except Exception:
			log.info("teamsChat: no se pudo enfocar el mensaje", exc_info=True)
		# El botón "Más opciones de mensajes" es el primer botón dentro del cuerpo.
		button = next((c for c in _children(msg.body) if _role(c) == Role.BUTTON), None)
		if button is not None:
			try:
				button.doAction()
				log.info(f"teamsChat: botón pulsado: {button.name!r}")
				return
			except Exception:
				log.info("teamsChat: no se pudo pulsar el botón de opciones", exc_info=True)
		else:
			log.info("teamsChat: el mensaje no tiene botón de opciones")
		# Alternativa: menú contextual con Mayúsculas+F10 sobre el mensaje enfocado.
		try:
			KeyboardInputGesture.fromName("shift+f10").send()
			log.info("teamsChat: enviado Mayúsculas+F10")
		except Exception:
			log.info("teamsChat: no se pudo abrir el menú del mensaje", exc_info=True)
			ui.message("No se pudo abrir el menú del mensaje")


def _positionText(index):
	return "el último" if index == 0 else f"{index + 1}º desde abajo"


def _makeScript(name, doc, handler):
	def script(self, gesture):
		handler(self)
	script.__name__ = f"script_{name}"
	script.__doc__ = doc
	script.category = SCRIPT_CATEGORY
	# Que también hable en el modo de voz "a petición".
	script.speakOnDemand = True
	# Marca para reconocer nuestros scripts en getScript.
	script.teamsChatScript = True
	return script


for _i in range(10):
	setattr(AppModule, f"script_readMessage{_i}", _makeScript(
		f"readMessage{_i}",
		f"Lee el mensaje {_i} del chat ({_positionText(_i)}). Dos pulsaciones lo copian.",
		lambda self, i=_i: self._readMessage(i),
	))
	setattr(AppModule, f"script_messageActions{_i}", _makeScript(
		f"messageActions{_i}",
		f"Muestra las acciones del mensaje {_i} del chat ({_positionText(_i)}): "
		"copiar, menú de Teams, enlaces y adjuntos.",
		lambda self, i=_i: self._showMessageActions(i),
	))
del _i

AppModule.script_focusComposeBox = _makeScript(
	"focusComposeBox",
	"Lleva el foco al cuadro de redacción del chat abierto.",
	lambda self: self._focusComposeBox(),
)
