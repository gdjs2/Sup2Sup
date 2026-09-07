"""One graphics scene for video and PGS, with clipping in source-pixel coordinates."""

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, qRgba
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsItemGroup, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView,
)

from sup2sup.edit.geometry import Crop, Transform
from sup2sup.pgs.parser import Cue


class CueGroup(QGraphicsItemGroup):
    def __init__(self, preview, cue_index, parent):
        super().__init__(parent)
        self.preview = preview
        self.cue_index = cue_index
        self.origin = QPointF()
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        self.origin = self.pos()
        self.preview.dragStarted.emit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        delta = self.pos() - self.origin
        dx, dy = round(delta.x()), round(delta.y())
        self.setPos(self.origin + QPointF(dx, dy))
        index, preview = self.cue_index, self.preview
        if dx or dy:
            # Let this mouse handler return before a model refresh deletes the old group.
            QTimer.singleShot(0, lambda: preview.cueMoved.emit(index, dx, dy))


class Preview(QGraphicsView):
    cueMoved = Signal(int, int, int)
    dragStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.setBackgroundBrush(QColor("#20242b"))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumSize(400, 240)
        self.root = QGraphicsRectItem()
        self.root.setBrush(QColor("black"))
        self.root.setPen(QPen(Qt.PenStyle.NoPen))
        self.root.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        scene.addItem(self.root)
        self.video_item = QGraphicsVideoItem(self.root)
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio)
        self.video_item.setZValue(0)
        self.group = None
        self.masks = []
        self.width_px, self.height_px = 1920, 1080
        self.crop = Crop()
        self.original = False
        self._tile_cue = None
        self._pixmaps = []
        self.configure(1920, 1080, Crop(), False)

    def configure(self, width, height, crop, original):
        self.width_px, self.height_px = width, height
        self.crop, self.original = crop, original
        region = crop.rectangle(width, height)
        canvas = QRectF(0, 0, width, height) if original else QRectF(
            region.x, region.y, region.width, region.height)
        self.root.setRect(canvas)
        self.scene().setSceneRect(canvas)
        for mask in self.masks:
            self.scene().removeItem(mask)
        self.masks.clear()
        if original:
            removed = [(0, 0, width, crop.top), (0, region.bottom, width, crop.bottom),
                       (0, crop.top, crop.left, region.height),
                       (region.right, crop.top, crop.right, region.height)]
            for x, y, w, h in removed:
                if w and h:
                    mask = QGraphicsRectItem(x, y, w, h, self.root)
                    mask.setBrush(QColor(210, 60, 50, 75))
                    mask.setPen(QPen(Qt.PenStyle.NoPen))
                    mask.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    mask.setZValue(2)
                    self.masks.append(mask)
            border = QGraphicsRectItem(region.x, region.y, region.width, region.height, self.root)
            pen = QPen(QColor("#67dfb3"))
            pen.setCosmetic(True)
            border.setPen(pen)
            border.setZValue(3)
            border.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.masks.append(border)
        self._fit()

    def set_video_rect(self, rect):
        self.video_item.setPos(rect.x, rect.y)
        self.video_item.setSize(QSizeF(rect.width, rect.height))

    def set_cue(self, cue: Cue | None, transform: Transform = Transform()):
        if self.group:
            self.scene().removeItem(self.group)
            self.group = None
        if cue is None:
            return
        if self._tile_cue is not cue:
            self._tile_cue = cue
            self._pixmaps = []
            colors = [qRgba(*color) for color in cue.palette]
            for placement in cue.placements:
                visible = placement.rect.intersection(placement.window)
                if visible is None:
                    continue
                bitmap, source = placement.bitmap, placement.source_rect
                # Qt expands the palette in native code, avoiding a Python loop over every pixel.
                image = QImage(bitmap.indices, bitmap.width, bitmap.height, bitmap.width,
                               QImage.Format.Format_Indexed8)
                image.setColorTable(colors)
                image = image.copy(source.x + visible.x - placement.rect.x,
                                   source.y + visible.y - placement.rect.y,
                                   visible.width, visible.height)
                self._pixmaps.append((visible.x, visible.y, QPixmap.fromImage(image)))
        self.group = CueGroup(self, cue.index, self.root)
        self.group.setZValue(1)
        for x, y, pixmap in self._pixmaps:
            item = QGraphicsPixmapItem(pixmap)
            item.setPos(x, y)
            self.group.addToGroup(item)
        if self.original:
            self.group.setFlags(QGraphicsItem.GraphicsItemFlag(0))
            self.group.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.group.setPos(transform.dx, transform.dy)
            # Thin outline makes transparent padded bitmaps and group extents discoverable.
            bounds = cue.bounds
            outline = QGraphicsRectItem(bounds.x, bounds.y, bounds.width, bounds.height)
            pen = QPen(QColor(103, 223, 179, 180), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            outline.setPen(pen)
            self.group.addToGroup(outline)
            # addToGroup preserves scene coordinates, so restore its local source position.
            outline.setPos(0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        if self.scene().sceneRect().isValid():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
