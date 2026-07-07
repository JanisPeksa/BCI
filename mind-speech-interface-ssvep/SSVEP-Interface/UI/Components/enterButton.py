from PyQt5.QtWidgets import QLabel,QStackedWidget,QLineEdit
from UI.Components.button_container import ButtonContainer
from server.twitterAPI import tweet

from playsound import playsound


from UI.UI_DEFS import getMainWidgetIndex

from UI.status import setOutputMode, getOutputMode

from UI.helperFunctions import disableOtherButtons, changeStacks

# TTS (Coqui) is optional and heavy; load it lazily so the UI can run without it.
try:
    from _TTS.watolink_TTS import TTS_synthesizer
    TTS = TTS_synthesizer(model_name = "tts_models/en/ljspeech/tacotron2-DDC")
except Exception as tts_error:
    print(f"TTS disabled ({tts_error}); voice output will be skipped.")
    TTS = None

class EnterButton(ButtonContainer):
    def __init__(self,parent):
        super().__init__(labelText="Confirm",freqName="Enter",checkable=False)
        self.setObjectName("Enter Button")
        self.clicked.connect(lambda: submitAndReturn(self,parent))

def submitAndReturn(self,parent):
    messageBox = parent.findChild(QLabel,"Prompt")
    mainStack = parent.findChild(QStackedWidget,"Main Widget")
    currWidget = mainStack.currentWidget()
    print(currWidget)
    inputField = parent.findChild(QLineEdit,"Input")

    if currWidget.objectName() == "Output Menu Page":
        navigateFromOutputMode(parent)
        return
    
    elif currWidget.objectName() == "Keyboard YN Menu Page":
        navigateFromHome(self,parent)
        return

    elif currWidget.objectName() == "Help Page":
        changeStacks(parent, getMainWidgetIndex("Output Menu Page"))
        return

    if inputField.text():
        temp = messageBox.text() + f"[{inputField.text()}]"
        #messageBox.setText(temp)
        if getOutputMode() == "Twitter":
            tweet(inputField.text())
        elif getOutputMode() == "Voice":
            if TTS is not None:
                TTS.synthesize(text = inputField.text())
            else:
                print("Voice output requested but TTS is disabled.")

        inputField.clear()

    # uncheck any checked boxes on submission
    for button in currWidget.findChildren(ButtonContainer):
        if button.isChecked():
            button.setChecked(False)

    # Go back to main page
    changeStacks(parent,getMainWidgetIndex("Keyboard YN Menu Page"))
    self.label.setText("Confirm")



def navigateFromOutputMode(parent):
    
    labels = ['Use Twitter','Use Voice']
    mainButtons = [parent.findChild(ButtonContainer,label) for label in labels]

    print("clicked")

    for button in mainButtons:
        if button.isChecked():
            if button.label.text() == labels[0]:
                # print("going to Twitter")
                setOutputMode("Twitter")
            elif button.label.text() == labels[1]:
                # print("going to Voice")
                setOutputMode("Voice")

            button.setChecked(False)
            changeStacks(parent,getMainWidgetIndex("Keyboard YN Menu Page"))
        

def navigateFromHome(self,parent):
    
    labels = ['Use Keyboard', 'Use Yes/No']
    mainButtons = [parent.findChild(ButtonContainer,label) for label in labels]

    for button in mainButtons:
        if button.label.text() == labels[0] and button.isChecked():
            # print("going to Keyboard")
            button.setChecked(False)
            changeStacks(parent,getMainWidgetIndex("Keyboard Page"))
            self.label.setText("Send message")

        elif button.label.text() == labels[1] and button.isChecked():
            # print("going to YN")
            button.setChecked(False)
            changeStacks(parent,getMainWidgetIndex("YN Page"))
            self.label.setText("Send message")
