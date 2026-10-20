# Teams Chat: panel de opciones en Preferencias > Opciones de NVDA.
# Va en un plugin global para que el panel exista aunque Teams no esté abierto.
# Copyright (C) 2026 Julio Villa. Distribuido bajo la GNU GPL versión 2 (ver LICENSE).

import config
import globalPluginHandler
import wx
from gui import guiHelper
from gui.settingsDialogs import NVDASettingsDialog, SettingsPanel

# Mantener igual que en appModules/ms-teams.py.
CONF_SECTION = "teamsChat"
config.conf.spec[CONF_SECTION] = {
	"numpadGestures": "boolean(default=True)",
	"attachmentAction": "option('viewer', 'browser', 'download', default='browser')",
}

# (valor guardado, texto en la lista), en el orden en que se muestran.
ATTACHMENT_ACTIONS = (
	("viewer", "Abrir en el visor de Teams"),
	("browser", "Abrir en el navegador"),
	("download", "Descargar"),
)


class TeamsChatSettingsPanel(SettingsPanel):
	title = "Teams Chat"

	def makeSettings(self, settingsSizer):
		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.numpadCheckBox = helper.addItem(
			wx.CheckBox(self, label="Usar Alt + &teclado numérico para leer mensajes (desactiva los códigos Alt en Teams)")
		)
		self.numpadCheckBox.SetValue(config.conf[CONF_SECTION]["numpadGestures"])
		self.attachmentChoice = helper.addLabeledControl(
			"&Acción por defecto para archivos adjuntos:",
			wx.Choice,
			choices=[label for _value, label in ATTACHMENT_ACTIONS],
		)
		values = [value for value, _label in ATTACHMENT_ACTIONS]
		self.attachmentChoice.SetSelection(values.index(config.conf[CONF_SECTION]["attachmentAction"]))

	def onSave(self):
		config.conf[CONF_SECTION]["numpadGestures"] = self.numpadCheckBox.IsChecked()
		config.conf[CONF_SECTION]["attachmentAction"] = ATTACHMENT_ACTIONS[self.attachmentChoice.GetSelection()][0]


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

	def __init__(self):
		super().__init__()
		NVDASettingsDialog.categoryClasses.append(TeamsChatSettingsPanel)

	def terminate(self):
		try:
			NVDASettingsDialog.categoryClasses.remove(TeamsChatSettingsPanel)
		except ValueError:
			pass
		super().terminate()
