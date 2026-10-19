# Teams Chat: panel de opciones en Preferencias > Opciones de NVDA.
# Va en un plugin global para que el panel exista aunque Teams no esté abierto.

import config
import globalPluginHandler
import wx
from gui import guiHelper
from gui.settingsDialogs import NVDASettingsDialog, SettingsPanel

CONF_SECTION = "teamsChat"
config.conf.spec[CONF_SECTION] = {
	"numpadGestures": "boolean(default=True)",
}


class TeamsChatSettingsPanel(SettingsPanel):
	title = "Teams Chat"

	def makeSettings(self, settingsSizer):
		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.numpadCheckBox = helper.addItem(
			wx.CheckBox(self, label="Usar Alt + &teclado numérico para leer mensajes (desactiva los códigos Alt en Teams)")
		)
		self.numpadCheckBox.SetValue(config.conf[CONF_SECTION]["numpadGestures"])

	def onSave(self):
		config.conf[CONF_SECTION]["numpadGestures"] = self.numpadCheckBox.IsChecked()


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
