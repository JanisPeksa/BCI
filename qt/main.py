import sys
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

class ClickableImageLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        self.player.setSource(QUrl.fromLocalFile("./assets/yesno.mp3"))
        
        self.audio_output.setVolume(1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.player.stop() 
            self.player.play()
            print("Image clicked! Playing sound...")

def main():
  app = QApplication(sys.argv)

  window = QWidget()
  window.setWindowTitle("BCI")
  window.resize(1024, 1200)

  layout = QVBoxLayout()

  image_label = ClickableImageLabel()

  pixmap = QPixmap("./assets/Untitled.png")

  scaled_pixmap = pixmap.scaled(
        1208, 558, 
        Qt.AspectRatioMode.KeepAspectRatio, 
        Qt.TransformationMode.SmoothTransformation
    )

  image_label.setPixmap(scaled_pixmap)
  image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

  image_label.setCursor(Qt.CursorShape.PointingHandCursor)

  layout.addWidget(image_label)
  window.setLayout(layout)

  window.show()

  sys.exit(app.exec())


if __name__ == "__main__":
    main()