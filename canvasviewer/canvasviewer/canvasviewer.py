from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QDesktopWidget, QApplication
from PyQt5.QtCore import Qt, QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QPalette

class Config:
    # 面板基本设置
    docker_id = 'pykrita_canvasviewer'
    
    # UI设置
    # UI设置
    min_width = 320
    min_height = 180
    margin = 2  # 布局边距
    screen_ratio = 2  # 面板最大尺寸为屏幕尺寸的几分之一
    
    # DPI矫正开关
    ENABLE_DPI_CORRECTION = True
    
    # 缩略图配置
    thumbnail_min_size = 140  # 缩略图最小尺寸
    thumbnail_upscale_factor = 2  # 缩略图放大倍数（用于两步缩放）
    use_hybrid_scaling = True  # 是否使用混合缩放（False则使用直接SmoothTransformation缩放）
    
    # 定时器配置（毫秒）
    state_check_interval = 100  # 状态检查间隔
    idle_check_interval = 300  # 空闲检查间隔
    idle_refresh_interval = 400  # 空闲刷新间隔
    
    # 面板位置
    dock_position = DockWidgetFactoryBase.DockRight
    
    @staticmethod
    def get_docker_name():
        # 使用QLocale获取当前语言环境
        from PyQt5.QtCore import QLocale
        current_locale = QLocale().name()
        # 根据语言设置返回对应的标题
        return '画布预览' if current_locale.startswith('zh_CN') else 'Canvas Viewer'
    
    # 标签设置
    @staticmethod
    def get_label_text():
        return Config.get_docker_name() + ' 面板'

    @staticmethod
    def get_max_size():
        screen = QDesktopWidget().screenGeometry()
        return screen.width() // Config.screen_ratio, screen.height() // Config.screen_ratio


class Worker(QObject):
    finished = pyqtSignal(QImage)
    
    def __init__(self, projection, width, height):
        super().__init__()
        self.projection = projection
        self.width = width
        self.height = height
    
    def process(self):
        try:
            # 物理像素尺寸 = 逻辑尺寸 * DPI 缩放因子
            dpi_scale = self.projection.devicePixelRatio()
            physical_width = int(self.width * dpi_scale)
            physical_height = int(self.height * dpi_scale)

            # 预处理（抗锯齿）
            smoothed = self.projection.scaled(
                self.projection.width() * 2, 
                self.projection.height() * 2,
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )

            # 第一步：FastTransformation 缩放到两倍目标物理尺寸
            double_width = physical_width * Config.thumbnail_upscale_factor
            double_height = physical_height * Config.thumbnail_upscale_factor
            fast_scaled = smoothed.scaled(
                double_width, double_height,
                Qt.KeepAspectRatio, 
                Qt.FastTransformation
            )

            # 第二步：SmoothTransformation 缩放到目标物理尺寸
            final_scaled = fast_scaled.scaled(
                physical_width, physical_height,
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )

            # 设置物理像素信息
            final_scaled.setDevicePixelRatio(dpi_scale)
            self.finished.emit(final_scaled)
        except Exception as e:
            print(f"Worker error: {str(e)}")
            self.finished.emit(QImage())


class Canvasviewer(DockWidget):
    # 添加类变量用于线程管理
    _is_thread_running = False
    _current_thread = None
    _current_worker = None
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(Config.get_docker_name())
        # 缓存当前缩略图数据
        self._current_thumbnail = None
        # 初始化状态检测相关变量
        self.state = 0  # 0: 正常状态, 1: 等待释放
        self.idle_state = False  # 空闲状态
        
        # 初始化定时器
        self._timer = QTimer()
        self._timer.timeout.connect(self.check_state)
        self._timer.start(Config.state_check_interval)  # 状态检查定时器
        
        self.idle_timer = QTimer()
        self.idle_timer.timeout.connect(self.enter_idle_state)
        
        self.idle_signal_timer = QTimer()
        self.idle_signal_timer.timeout.connect(self.send_idle_signal)
        
        # 监听主题变化
        app = QApplication.instance()
        app.paletteChanged.connect(self.update_theme_color)
        
        self.initUI()
        print("CanvasViewer 已初始化")

    def update_theme_color(self):
        # 获取当前应用的调色板
        app = QApplication.instance()
        # 获取当前主题的Window角色颜色作为倒数第二深的颜色
        window_color = app.palette().color(QPalette.Window)
        # 更新缩略图标签的背景色
        margin = Config.margin
        self.thumbnail_label.setStyleSheet(
            f"background-color: {window_color.name()}; padding: {margin}px;"
        )

    def initUI(self):
        # 创建主布局
        widget = QWidget()
        widget.setMinimumSize(Config.min_width, Config.min_height)
        max_width, max_height = Config.get_max_size()
        widget.setMaximumSize(max_width, max_height)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(Config.margin, Config.margin, Config.margin, Config.margin)
        
        # 创建缩略图标签
        self.thumbnail_label = QLabel(Config.get_label_text())
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.thumbnail_label)
        
        # 应用主题色
        self.update_theme_color()

        # 设置主窗口部件
        self.setWidget(widget)
        
        # 初始显示缩略图
        self.refresh_thumbnail()

    def get_thumbnail_size(self):
        # 获取缩略图标签尺寸
        label_size = self.thumbnail_label.size()
        # 新增边距计算，添加安全检查
        margin = min(Config.margin, 10)  # 限制边距最大值为10，防止过大导致崩溃
        
        # 确保可用尺寸不会为负值
        available_width = max(1, self.thumbnail_label.width() - margin*2)  # 减去左右边距，确保至少为1
        available_height = max(1, self.thumbnail_label.height() - margin*2)  # 减去上下边距，确保至少为1

        # 获取DPI缩放因子
        dpi_scale = self.devicePixelRatioF() if Config.ENABLE_DPI_CORRECTION else 1.0
        
        doc = Krita.instance().activeDocument()
        if not doc:
            return int(min(available_width/dpi_scale)), int(min(available_height/dpi_scale))

        # 使用物理像素计算
        canvas_width = doc.width() * dpi_scale
        canvas_height = doc.height() * dpi_scale
        
        # 计算画布的宽高比
        canvas_ratio = doc.width() / doc.height()
        label_ratio = label_size.width() / label_size.height()
        
        if canvas_ratio > label_ratio:
            # 画布更宽，以label宽度为基准
            width = label_size.width() * dpi_scale
            height = int(width / canvas_ratio)
        else:
            # 画布更高，以label高度为基准
            height = label_size.height() * dpi_scale
            width = int(height * canvas_ratio)
        
        # 返回逻辑像素尺寸
        return int(width / dpi_scale), int(height / dpi_scale)

    def refresh_thumbnail(self):
        doc = Krita.instance().activeDocument()
        if not doc:
            self.thumbnail_label.setText('没有打开的文档')
            return
            
        # 如果已有线程在运行，尝试先停止它
        if Canvasviewer._is_thread_running:
            try:
                # 安全地停止当前线程
                if Canvasviewer._current_thread and Canvasviewer._current_thread.isRunning():
                    Canvasviewer._current_thread.quit()
                    # 给线程一点时间退出，但不阻塞UI
                    if not Canvasviewer._current_thread.wait(100):
                        print("警告: 线程未能及时退出")
                        # 重置线程状态但不强制终止
                        Canvasviewer._is_thread_running = False
                        return
            except Exception as e:
                print(f"停止线程错误: {str(e)}")
                # 重置线程状态
                Canvasviewer._is_thread_running = False
                return
            
        # 获取DPI缩放因子
        dpi_scale = self.devicePixelRatioF() if Config.ENABLE_DPI_CORRECTION else 1.0
        
        # 使用物理像素捕获投影
        projection = doc.projection(0, 0, doc.width(), doc.height())
        projection.setDevicePixelRatio(dpi_scale)
        
        # 获取目标尺寸
        thumb_width, thumb_height = self.get_thumbnail_size()
        
        try:
            # 创建worker和thread
            Canvasviewer._current_worker = Worker(projection, thumb_width, thumb_height)
            Canvasviewer._current_thread = QThread()
            Canvasviewer._current_worker.moveToThread(Canvasviewer._current_thread)
            
            # 连接信号和槽
            Canvasviewer._current_thread.started.connect(Canvasviewer._current_worker.process)
            Canvasviewer._current_worker.finished.connect(self.on_worker_finished)
            
            # 设置线程运行标志
            Canvasviewer._is_thread_running = True
            
            # 确保线程在Krita主事件循环中运行
            Canvasviewer._current_thread.setObjectName("ThumbnailWorkerThread")
            Canvasviewer._current_thread.start(QThread.LowPriority)  # 使用低优先级
        except Exception as e:
            print(f"创建线程错误: {str(e)}")
            # 重置线程状态
            Canvasviewer._is_thread_running = False

    def on_worker_finished(self, image):
        # 更新缩略图
        self.update_thumbnail(image)
        
        # 清理线程资源
        try:
            if Canvasviewer._current_thread and Canvasviewer._current_worker:
                # 断开所有连接，使用try-except防止断开不存在的连接
                try:
                    if Canvasviewer._current_thread.started.receivers() > 0:
                        Canvasviewer._current_thread.started.disconnect()
                except Exception as e:
                    print(f"断开线程started信号错误: {str(e)}")
                    
                try:
                    if Canvasviewer._current_worker.finished.receivers() > 0:
                        Canvasviewer._current_worker.finished.disconnect()
                except Exception as e:
                    print(f"断开worker finished信号错误: {str(e)}")
                
                # 结束线程，添加超时处理
                Canvasviewer._current_thread.quit()
                # 等待最多200毫秒
                if not Canvasviewer._current_thread.wait(200):
                    print("警告: 线程未能在超时时间内结束")
                
                # 删除对象
                Canvasviewer._current_worker.deleteLater()
                Canvasviewer._current_thread.deleteLater()
        except Exception as e:
            print(f"清理线程资源错误: {str(e)}")
            # 在异常情况下显示错误信息
            self.thumbnail_label.setText('没有打开的文档')
        finally:
            # 无论如何都重置引用和标志，确保下一次可以创建新线程
            Canvasviewer._current_worker = None
            Canvasviewer._current_thread = None
            Canvasviewer._is_thread_running = False

    def update_thumbnail(self, image):
        try:
            if not image.isNull():
                # 将QImage转换为QPixmap并显示
                pixmap = QPixmap.fromImage(image)
                self.thumbnail_label.setPixmap(pixmap)
                self._current_thumbnail = image
        except Exception as e:
            print(f"Update thumbnail error: {str(e)}")

    def update_thumbnail_display(self):
        if not self._current_thumbnail:
            return
            
        # 获取当前需要的显示尺寸
        thumb_width, thumb_height = self.get_thumbnail_size()
        
        # 缩放图像并显示
        scaled_image = self._current_thumbnail.scaled(thumb_width, thumb_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap = QPixmap.fromImage(scaled_image)
        self.thumbnail_label.setPixmap(pixmap)

    def check_state(self):
        # 检测鼠标状态
        mouse_buttons = QApplication.mouseButtons()
        is_left_pressed = bool(mouse_buttons & Qt.LeftButton)
        is_right_pressed = bool(mouse_buttons & Qt.RightButton)
        is_middle_pressed = bool(mouse_buttons & Qt.MiddleButton)

        # 更新状态机
        if self.state == 0:
            if not is_left_pressed and not is_right_pressed and not is_middle_pressed:
                self.state = 1
                # 只有在没有线程运行且不在空闲状态时才刷新
                if not Canvasviewer._is_thread_running and not self.idle_state:
                    self.refresh_thumbnail()
                self.idle_timer.start(Config.idle_check_interval)
        elif self.state == 1:
            if is_left_pressed or is_right_pressed or is_middle_pressed:
                self.state = 0
                if self.idle_state:
                    self.idle_state = False
                self.idle_timer.stop()

    def enter_idle_state(self):
        if not self.idle_state:
            self.idle_state = True
            self.idle_signal_timer.start(Config.idle_refresh_interval)  # 空闲刷新定时器

    def send_idle_signal(self):
        if self.idle_state:
            # 只有在没有线程运行时才刷新
            if not Canvasviewer._is_thread_running:
                self.refresh_thumbnail()
        else:
            self.idle_signal_timer.stop()

    def canvasChanged(self, canvas):
        # 画布改变时刷新缩略图
        self.refresh_thumbnail()


instance = Krita.instance()
dock_widget_factory = DockWidgetFactory(Config.docker_id,
                                        Config.dock_position,
                                        Canvasviewer)

instance.addDockWidgetFactory(dock_widget_factory)
