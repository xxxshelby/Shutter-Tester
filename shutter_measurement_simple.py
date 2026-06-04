import tkinter as tk
from tkinter import ttk
import serial
import serial.tools.list_ports
import threading
import time
import queue
import re

# ==================== 极简快门测量器 ====================
class SimpleShutterMeter:
    def __init__(self, root):
        self.root = root
        self.root.title("快门测量器")
        self.root.geometry("400x350")
        
        # 串口设备
        self.serial_port = None
        self.is_connected = False
        self.data_queue = queue.Queue()
        self.thread_running = False
        
        # 当前测量值
        self.current_value = 0.0
        
        # 颜色方案
        self.colors = {
            'bg': '#f0f0f0',
            'card': '#ffffff',
            'primary': '#2c3e50',
            'connected': '#27ae60',
            'disconnected': '#e74c3c',
            'text': '#2c3e50',
            'text_light': '#7f8c8d',
            'value': '#2c3e50'
        }
        
        # 初始化GUI
        self.setup_gui()
        
        # 启动串口数据处理器
        self.process_serial_data()
    
    def setup_gui(self):
        """设置极简GUI"""
        # 主容器
        main_frame = tk.Frame(self.root, bg=self.colors['bg'], padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 标题
        title_frame = tk.Frame(main_frame, bg=self.colors['bg'])
        title_frame.pack(fill=tk.X, pady=(0, 20))
        
        tk.Label(
            title_frame,
            text="⏱️ 快门测量器",
            font=('微软雅黑', 18, 'bold'),
            bg=self.colors['bg'],
            fg=self.colors['primary']
        ).pack()
        
        # 连接控制区
        self.setup_connection_area(main_frame)
        
        # 测量值显示区
        self.setup_display_area(main_frame)
        
        # 状态信息
        self.setup_status_area(main_frame)
    
    def setup_connection_area(self, parent):
        """设置连接控制区域"""
        conn_frame = tk.Frame(parent, bg=self.colors['bg'])
        conn_frame.pack(fill=tk.X, pady=(0, 20))
        
        # 两列布局
        left_frame = tk.Frame(conn_frame, bg=self.colors['bg'])
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        right_frame = tk.Frame(conn_frame, bg=self.colors['bg'])
        right_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 端口选择
        tk.Label(
            left_frame,
            text="串口:",
            font=('微软雅黑', 10),
            bg=self.colors['bg'],
            fg=self.colors['text']
        ).pack(anchor='w')
        
        # 端口下拉框
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            left_frame,
            textvariable=self.port_var,
            state="readonly",
            width=15,
            font=('微软雅黑', 10)
        )
        self.port_combo.pack(anchor='w', pady=(5, 0))
        
        # 扫描按钮
        tk.Button(
            right_frame,
            text="扫描",
            command=self.scan_ports,
            font=('微软雅黑', 9),
            bg=self.colors['primary'],
            fg='white',
            relief=tk.RAISED,
            cursor='hand2',
            padx=12,
            pady=2
        ).pack(side=tk.TOP, pady=(8, 0))
        
        # 连接/断开按钮
        self.connect_btn = tk.Button(
            right_frame,
            text="连接",
            command=self.toggle_connection,
            font=('微软雅黑', 9, 'bold'),
            bg=self.colors['disconnected'],
            fg='white',
            relief=tk.RAISED,
            cursor='hand2',
            padx=12,
            pady=2
        )
        self.connect_btn.pack(side=tk.TOP, pady=(5, 0))
        
        # 初始化扫描
        self.scan_ports()
    
    def setup_display_area(self, parent):
        """设置显示区域"""
        display_frame = tk.Frame(parent, bg=self.colors['card'], bd=2, relief=tk.SOLID)
        display_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 20))
        
        # 测量值显示
        value_frame = tk.Frame(display_frame, bg=self.colors['card'], padx=20, pady=20)
        value_frame.pack(fill=tk.BOTH, expand=True)
        
        self.value_label = tk.Label(
            value_frame,
            text="等待连接...",
            font=('Consolas', 48, 'bold'),
            bg=self.colors['card'],
            fg=self.colors['value']
        )
        self.value_label.pack(expand=True)
    
    def setup_status_area(self, parent):
        """设置状态信息区域"""
        status_frame = tk.Frame(parent, bg=self.colors['bg'])
        status_frame.pack(fill=tk.X)
        
        self.status_label = tk.Label(
            status_frame,
            text="状态: 未连接",
            font=('微软雅黑', 9),
            bg=self.colors['bg'],
            fg=self.colors['text_light']
        )
        self.status_label.pack(anchor='w')
    
    def format_shutter_speed(self, ms_value: float) -> str:
        """格式化快门速度显示
        
        Args:
            ms_value: 毫秒值
            
        Returns:
            格式化后的快门速度字符串：
            - ≥500ms: 显示为 x.xxx" (秒，保留3位小数)
            - <500ms: 显示为 1/xxx (近似值，四舍五入到整数)
        """
        if ms_value >= 500:  # 500ms及以上
            # 转换为秒，显示为 x.xxx"
            seconds = ms_value / 1000
            return f"{seconds:.3f}\""
        else:  # 500ms以下
            # 计算近似的分数表示
            if ms_value > 0:
                denominator = round(1000 / ms_value)
                if denominator <= 0:
                    denominator = 1
                return f"1/{denominator}"
            else:
                return "0"
    
    def scan_ports(self):
        """扫描可用串口"""
        try:
            ports = list(serial.tools.list_ports.comports())
            
            if not ports:
                self.port_combo['values'] = ["未找到串口"]
                self.port_combo.set("未找到串口")
            else:
                port_list = [port.device for port in ports]
                self.port_combo['values'] = port_list
                if port_list:
                    self.port_combo.current(0)
            
        except Exception as e:
            print(f"扫描串口失败: {e}")
    
    def toggle_connection(self):
        """切换连接状态"""
        if not self.is_connected:
            self.connect_device()
        else:
            self.disconnect_device()
    
    def connect_device(self):
        """连接设备"""
        selected = self.port_var.get()
        if not selected or "未找到串口" in selected:
            return
        
        try:
            # 关闭已有连接
            if self.serial_port and hasattr(self.serial_port, 'is_open') and self.serial_port.is_open:
                self.serial_port.close()
            
            # 创建新连接
            self.serial_port = serial.Serial(
                port=selected,
                baudrate=115200,
                timeout=1
            )
            
            # 启动读取线程
            self.thread_running = True
            self.read_thread = threading.Thread(target=self.read_serial, daemon=True)
            self.read_thread.start()
            
            # 更新状态
            self.is_connected = True
            self.update_connection_ui()
            
        except Exception as e:
            print(f"连接失败: {e}")
    
    def disconnect_device(self):
        """断开设备连接"""
        if not self.is_connected:
            return
        
        # 停止读取线程
        self.thread_running = False
        
        # 关闭串口
        if self.serial_port and hasattr(self.serial_port, 'is_open') and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except:
                pass
        
        # 更新状态
        self.is_connected = False
        self.update_connection_ui()
        
        # 重置显示
        self.value_label.config(text="已断开")
    
    def update_connection_ui(self):
        """更新连接UI"""
        if self.is_connected:
            self.connect_btn.config(
                text="断开",
                bg=self.colors['connected']
            )
            self.status_label.config(
                text=f"状态: 已连接",
                fg=self.colors['connected']
            )
        else:
            self.connect_btn.config(
                text="连接",
                bg=self.colors['disconnected']
            )
            self.status_label.config(
                text="状态: 未连接",
                fg=self.colors['text_light']
            )
    
    def read_serial(self):
        """读取串口数据"""
        print("开始读取串口数据...")
        while self.thread_running and self.serial_port and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting:
                    data = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                    if data:
                        self.data_queue.put(data)
            except:
                break
            time.sleep(0.01)
        print("串口读取线程结束")
    
    def process_serial_data(self):
        """处理串口数据"""
        try:
            if not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                self.process_measurement_data(data)
        except queue.Empty:
            pass
        
        # 继续处理
        self.root.after(50, self.process_serial_data)
    
    def process_measurement_data(self, data: str):
        """处理测量数据"""
        # 清理数据
        data = data.strip()
        
        # 匹配 x.xxx 格式的测量值
        match = re.match(r'^(\d+\.\d{3})$', data)
        if not match:
            return
        
        try:
            # 解析测量值
            value = float(match.group(1))
            self.current_value = value
            
            # 转换为快门速度显示
            speed_display = self.format_shutter_speed(value)
            
            # 更新显示
            self.value_label.config(text=speed_display)
            
            print(f"收到测量值: {value:.3f}ms -> {speed_display}")
            
        except Exception as e:
            print(f"数据处理错误: {e}")

# ==================== 主程序 ====================
def main():
    root = tk.Tk()
    app = SimpleShutterMeter(root)
    
    # 窗口关闭事件
    def on_closing():
        app.disconnect_device()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()