# Teams Chat: módulo de aplicación para el nuevo Microsoft Teams (ms-teams.exe).
# Solo se carga cuando Teams está activo, así que no afecta al resto de NVDA.

import re
import time

import api
import appModuleHandler
import config
import ui
from controlTypes import Role
from keyboardHandler import KeyboardInputGesture
from logHandler import log

CONF_SECTION = "teamsChat"
config.conf.spec[CONF_SECTION] = {
	"numpadGestures": "boolean(default=True)",
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
			names = ", ".join(name for name, _url in self.attachments)
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
		attachments.append((name, match.group(0) if match else None))
	return attachments


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
		**{f"kb:NVDA+alt+{i}": f"messageMenu{i}" for i in range(10)},
		**{f"kb:NVDA+alt+numLockNumpad{i}": f"messageMenu{i}" for i in range(10)},
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
		ti = getattr(api.getFocusObject(), "treeInterceptor", None)
		try:
			return ti.rootNVDAObject.name if ti else None
		except Exception:
			return None

	def _findChatList(self):
		"""Devuelve (lista, método usado) o (None, None)."""
		pane = None
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
				obj = obj.parent
		docName = self._currentDocName()
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
			toCopy = msg.text or "\n".join(url for _file, url in msg.attachments if url)
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

	def _openMessageMenu(self, index):
		log.info(f"teamsChat: menú del mensaje {index} solicitado")
		msg, _chatList = self._locateMessage(index)
		if msg is None:
			return
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
	setattr(AppModule, f"script_messageMenu{_i}", _makeScript(
		f"messageMenu{_i}",
		f"Abre el menú de opciones del mensaje {_i} del chat ({_positionText(_i)}).",
		lambda self, i=_i: self._openMessageMenu(i),
	))
del _i
