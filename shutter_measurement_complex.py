import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import threading
import time
import queue
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import json
from datetime import datetime

# ==================== 数据模型 ====================
class AppState(Enum):
    """应用程序状态"""
    DISCONNECTED = "disconnected"      # 未连接
    CONNECTED = "connected"            # 已连接
    VERIFYING = "verifying"            # 验证中
    MEASURING = "measuring"            # 测量中
    COMPLETING = "completing"          # 正在完成

@dataclass
class MeasurementConfig:
    """测量配置"""
    speed_str: str           # 快门速度字符串，如 "1/125"
    count: int               # 测量次数
    expected_ms: float       # 预期时间(毫秒)
    
    @classmethod
    def from_input(cls, speed_str: str, count: int) -> 'MeasurementConfig':
        """从用户输入创建配置"""
        expected_ms = cls.speed_to_ms(speed_str)
        return cls(speed_str, count, expected_ms)
    
    @staticmethod
    def speed_to_ms(speed_str: str) -> float:
        """将快门速度字符串转换为毫秒"""
        try:
            if '/' in speed_str:
                parts = speed_str.split('/')
                if len(parts) == 2:
                    numerator = float(parts[0])
                    denominator = float(parts[1])
                    if denominator == 0:
                        return 0
                    return (numerator / denominator) * 1000
            else:
                # 处理如 "1s", "0.5s" 等格式
                speed_str = speed_str.replace('s', '').strip()
                return float(speed_str) * 1000
        except:
            return 0
    
    def is_high_speed(self) -> bool:
        """判断是否为高速快门（高于1/100s）"""
        return self.expected_ms <= 1000/120  # 1/100s = 10ms

@dataclass
class MeasurementResult:
    """单次测量结果"""
    config: MeasurementConfig
    values: List[float] = field(default_factory=list)  # 原始测量值列表
    adjusted_values: List[float] = field(default_factory=list)  # 调整后的测量值列表
    average: Optional[float] = None                    # 调整后的平均值
    start_time: Optional[datetime] = None              # 开始时间
    end_time: Optional[datetime] = None                # 结束时间
    
    def add_value(self, value: float):
        """添加测量值"""
        # 存储原始值
        self.values.append(value)
        
        # 根据快门速度调整值
        if self.config.is_high_speed():
            adjusted_value = value
        else:
            # 低速快门：直接使用
            adjusted_value = value
        
        self.adjusted_values.append(adjusted_value)
        
        if len(self.adjusted_values) == self.config.count:
            self.average = sum(self.adjusted_values) / len(self.adjusted_values)
            self.end_time = datetime.now()
    
    def get_error(self) -> Optional[float]:
        """计算误差百分比（基于调整后的值）"""
        if self.average is None or self.config.expected_ms == 0:
            return None
        return ((self.average - self.config.expected_ms) / self.config.expected_ms) * 100
    
    def get_error_level(self) -> str:
        """获取误差级别"""
        error = self.get_error()
        if error is None:
            return "unknown"
        error_abs = abs(error)
        if error_abs < 5:
            return "good"     # 绿色
        elif error_abs < 10:
            return "fair"     # 黄色
        else:
            return "poor"     # 红色

@dataclass
class SerialDevice:
    """串口设备"""
    port: Optional[serial.Serial] = None
    is_open: bool = False
    thread_running: bool = False
    read_thread: Optional[threading.Thread] = None
    data_queue: queue.Queue = field(default_factory=queue.Queue)

# ==================== 主应用程序 ====================
class ShutterMeasurementApp:
    def __init__(self, root):
        self.root = root
        self.root.title("快门测量诊断系统")
        self.root.geometry("1000x700")
        self.root.configure(bg='#1a1a1a')
        
        # 状态管理
        self.state = AppState.DISCONNECTED
        self.current_result: Optional[MeasurementResult] = None
        self.history_results: List[MeasurementResult] = []
        
        # 串口设备
        self.device = SerialDevice()
        
        # 测量控制
        self.measurement_completed = False
        self.pending_ui_updates = []
        
        # UI 颜色定义
        self.colors = {
            'primary': '#2c3e50',
            'secondary': '#3498db',
            'success': '#27ae60',
            'warning': '#f39c12',
            'danger': '#e74c37',
            'dark': '#1a1a1a',
            'light': '#f5f5f5',
            'bg_dark': '#1a1a1a',
            'bg_card': '#2d2d2d',
            'text': '#ffffff',
            'text_secondary': '#b0bec5',
            'accent': '#4fc3f7',
            'border': '#404040',
            'error_good': '#81c784',
            'error_fair': '#ffb74d',
            'error_poor': '#e57373'
        }
        
        # 按钮颜色映射
        self.button_colors = {
            'scan': self.colors['secondary'],
            'connect': self.colors['success'],
            'disconnect': self.colors['danger'],
            'verify': self.colors['warning'],
            'start': self.colors['success'],
            'stop': self.colors['danger']
        }
        
        # 存储结果卡片的引用
        self.result_cards: Dict[str, Dict[str, Any]] = {}  # speed_str -> card widgets
        
        # 卡片尺寸
        self.card_width = 250
        self.card_height = 150
        self.card_padding = 10
        
        # 创建界面
        self.create_gui()
        
        # 启动数据处理器
        self.process_data_loop()
    
    # ==================== 状态管理 ====================
    def change_state(self, new_state: AppState):
        """改变应用程序状态"""
        old_state = self.state
        self.state = new_state
        print(f"状态变更: {old_state.value} -> {new_state.value}")
        self.update_ui_for_state()
    
    def update_ui_for_state(self):
        """根据当前状态更新UI"""
        # 更新按钮状态
        self.update_button_states()
        
        # 更新状态指示
        self.update_status_indicator()
    
    def update_button_states(self):
        """根据状态更新所有按钮状态"""
        state = self.state
        
        # 扫描按钮 - 只有不在测量/验证/完成时可用
        self.scan_btn.config(
            state='normal' if state not in [AppState.MEASURING, AppState.VERIFYING, AppState.COMPLETING] else 'disabled'
        )
        
        # 端口选择框状态
        self.port_combo.configure(
            state='readonly' if state not in [AppState.MEASURING, AppState.VERIFYING, AppState.COMPLETING] else 'disabled'
        )
        
        # 连接按钮 - 只有断开状态且选择了端口可用
        if state == AppState.DISCONNECTED:
            selected = self.port_combo.get()
            has_valid_port = selected and selected != "" and "未找到串口" not in selected
            self.connect_btn.config(state='normal' if has_valid_port else 'disabled')
        else:
            self.connect_btn.config(state='disabled')
        
        # 断开按钮 - 只有连接状态可用
        self.disconnect_btn.config(
            state='normal' if state in [AppState.CONNECTED, AppState.MEASURING, AppState.VERIFYING, AppState.COMPLETING] else 'disabled'
        )
        
        # 验证按钮 - 只有连接状态可用
        self.verify_btn.config(
            state='normal' if state == AppState.CONNECTED else 'disabled'
        )
        
        # 开始测量按钮 - 只有连接状态可用
        self.start_btn.config(
            state='normal' if state == AppState.CONNECTED else 'disabled'
        )
        
        # 停止按钮 - 只有测量状态可用
        self.stop_btn.config(
            state='normal' if state == AppState.MEASURING else 'disabled'
        )
        
        # 输入框状态
        input_state = 'normal' if state == AppState.CONNECTED else 'disabled'
        self.speed_entry.config(state=input_state)
        self.count_spin.config(state=input_state)
        
        # 立即刷新界面
        self.root.update_idletasks()
    
    def update_status_indicator(self):
        """更新状态指示器"""
        status_configs = {
            AppState.DISCONNECTED: ("未连接", "#666666"),
            AppState.CONNECTED: ("已连接", self.colors['success']),
            AppState.VERIFYING: ("验证中...", self.colors['warning']),
            AppState.MEASURING: ("测量中...", self.colors['accent']),
            AppState.COMPLETING: ("正在完成...", self.colors['accent'])
        }
        
        text, color = status_configs.get(self.state, ("未知", "#666666"))
        self.status_label.config(text=text)
        self.status_indicator.itemconfig(self.status_circle, fill=color)
    
    # ==================== 串口操作 ====================
    def scan_ports(self):
        """扫描可用串口"""
        if self.state in [AppState.MEASURING, AppState.VERIFYING, AppState.COMPLETING]:
            return
            
        try:
            ports = list(serial.tools.list_ports.comports())
            
            if not ports:
                self.port_combo['values'] = ["未找到串口"]
                self.port_combo.set("未找到串口")
            else:
                port_list = [f"{port.device} - {port.description}" for port in ports]
                self.port_combo['values'] = port_list
                if port_list:
                    self.port_combo.current(0)
            
            # 扫描后更新按钮状态
            self.update_button_states()
            
        except Exception as e:
            messagebox.showerror("错误", f"扫描串口失败: {str(e)}")
    
    def connect_device(self):
        """连接串口设备"""
        if self.state != AppState.DISCONNECTED:
            return
            
        selected = self.port_combo.get()
        if not selected or "未找到串口" in selected:
            messagebox.showwarning("警告", "请先选择有效的串口")
            return
        
        try:
            port_name = selected.split(" - ")[0]
            print(f"尝试连接串口: {port_name}")
            
            # 关闭已存在的连接
            if self.device.port and hasattr(self.device.port, 'is_open') and self.device.port.is_open:
                self.device.port.close()
            
            # 创建新连接
            self.device.port = serial.Serial(
                port=port_name,
                baudrate=115200,
                timeout=1
            )
            self.device.is_open = True
            
            # 启动数据读取线程
            self.device.thread_running = True
            self.device.read_thread = threading.Thread(
                target=self.read_serial_data,
                daemon=True
            )
            self.device.read_thread.start()
            
            # 更新状态
            self.change_state(AppState.CONNECTED)
            self.verify_status.config(text="○ 未验证", fg='#666666')
            
            print(f"串口连接成功: {port_name}")
            
        except Exception as e:
            messagebox.showerror("错误", f"连接失败: {str(e)}")
            self.device.is_open = False
            self.device.port = None
    
    def disconnect_device(self):
        """断开串口连接"""
        if self.state == AppState.DISCONNECTED:
            return
            
        # 停止测量
        if self.state in [AppState.MEASURING, AppState.COMPLETING]:
            self.stop_measurement()
        
        # 停止读取线程
        self.device.thread_running = False
        
        # 关闭串口
        if self.device.port and hasattr(self.device.port, 'is_open') and self.device.port.is_open:
            try:
                self.device.port.close()
            except:
                pass
        
        # 重置设备状态
        self.device.is_open = False
        self.device.port = None
        
        # 更新状态
        self.change_state(AppState.DISCONNECTED)
        
        print("设备已断开连接")
    
    def read_serial_data(self):
        """读取串口数据"""
        print("串口数据读取线程启动")
        while self.device.thread_running and self.device.port and self.device.is_open:
            try:
                if self.device.port.in_waiting:
                    data = self.device.port.readline().decode('utf-8', errors='ignore')
                    if data:
                        self.device.data_queue.put(data)
            except:
                break
            time.sleep(0.01)
        print("串口数据读取线程结束")
    
    def process_data_loop(self):
        """处理接收到的数据"""
        try:
            if not self.device.data_queue.empty():
                data = self.device.data_queue.get_nowait()
                self.process_serial_data(data)
        except queue.Empty:
            pass
        
        self.root.after(100, self.process_data_loop)
    
    def process_serial_data(self, data: str):
        """处理串口数据"""
        # 匹配 x.xxx 格式
        match = re.match(r'^\d+\.\d{3}$', data.strip())
        if not match:
            return
        
        value = float(data.strip())
        print(f"收到测量值: {value}ms")
        
        # 根据当前状态处理数据
        if self.state == AppState.VERIFYING:
            self.handle_verification_data(value)
        elif self.state == AppState.MEASURING and self.current_result:
            # 只处理指定次数的测量
            if len(self.current_result.values) < self.current_result.config.count:
                self.handle_measurement_data(value)
            else:
                print(f"已收到足够的测量数据({self.current_result.config.count}次)，忽略额外数据")
    
    # ==================== 验证操作 ====================
    def verify_device(self):
        """验证设备"""
        if self.state != AppState.CONNECTED:
            return
            
        self.change_state(AppState.VERIFYING)
        
        # 10秒超时
        def verification_timeout():
            if self.state == AppState.VERIFYING:
                self.change_state(AppState.CONNECTED)
                messagebox.showwarning("超时", "验证超时，请检查设备并重试")
        
        self.root.after(10000, verification_timeout)
    
    def handle_verification_data(self, value: float):
        """处理验证数据"""
        self.change_state(AppState.CONNECTED)
        self.verify_status.config(text="✓ 已验证", fg=self.colors['success'])
        messagebox.showinfo("验证成功", f"设备验证成功，收到数据: {value:.3f}ms")
    
    # ==================== 测量操作 ====================
    def start_measurement(self):
        """开始测量"""
        if self.state != AppState.CONNECTED:
            return
        
        # 获取用户输入
        speed_str = self.speed_var.get().strip()
        try:
            count = int(self.count_var.get())
        except:
            count = 3
        
        # 验证输入
        if not speed_str:
            messagebox.showwarning("警告", "请输入快门速度")
            return
        
        config = MeasurementConfig.from_input(speed_str, count)
        if config.expected_ms == 0:
            messagebox.showwarning("警告", "无效的快门速度格式")
            return
        
        # 检查是否已存在该速度的测量结果
        card_id = f"card_{speed_str}"
        if card_id in self.result_cards:
            # 如果已存在，询问是否重新测量
            if not messagebox.askyesno("确认", f"已存在 {speed_str} 的测量结果，是否重新测量？"):
                return
            # 删除旧的卡片
            old_card = self.result_cards[card_id]
            if 'canvas_id' in old_card:
                self.results_canvas.delete(old_card['canvas_id'])
            old_card['frame'].destroy()
            del self.result_cards[card_id]
            
            # 重新排列所有卡片
            self.rearrange_cards()
        
        # 创建测量结果对象
        self.current_result = MeasurementResult(config)
        self.current_result.start_time = datetime.now()
        self.measurement_completed = False
        
        # 更新UI
        # 不再清除已有结果，只更新进度显示
        self.speed_display.config(text=speed_str, fg=self.colors['accent'])
        self.measure_info.config(text="等待触发第 1 次快门...")
        self.update_progress(0)
        
        # 创建结果卡片
        self.create_result_card(config)
        
        # 改变状态
        self.change_state(AppState.MEASURING)
        
        print(f"开始测量: {speed_str}, 预期: {config.expected_ms:.1f}ms, 次数: {count}")
    
    def handle_measurement_data(self, value: float):
        """处理测量数据"""
        if not self.current_result or self.measurement_completed:
            return
        
        adjusted_value = value
        display_text = f"{adjusted_value:.3f}ms"
        
        # 添加测量值
        self.current_result.add_value(value)
        
        # 更新进度
        progress = len(self.current_result.values) / self.current_result.config.count * 100
        self.update_progress(progress)
        
        # 更新结果卡片
        self.update_result_card()
        
        # 更新测量信息
        self.measure_info.config(text=f"第 {len(self.current_result.values)} 次测得: {display_text}")
        print(f"第 {len(self.current_result.values)} 次测量: 原始{value:.3f}ms, 显示{display_text}")
        
        # 检查是否完成
        if len(self.current_result.values) == self.current_result.config.count:
            # 标记测量完成
            self.measurement_completed = True
            # 立即开始完成测量
            self.root.after(100, self.finish_measurement)
        elif len(self.current_result.values) < self.current_result.config.count:
            # 安全地设置下一次提示
            current_count = len(self.current_result.values)
            def set_next_prompt():
                if self.current_result and current_count < self.current_result.config.count:
                    self.measure_info.config(text=f"请触发第 {current_count + 1} 次快门...")
            
            self.root.after(500, set_next_prompt)
    
    def stop_measurement(self):
        """停止测量"""
        if self.state not in [AppState.MEASURING, AppState.COMPLETING]:
            return
        
        self.measurement_completed = True
        self.change_state(AppState.CONNECTED)
        self.speed_display.config(text="已停止", fg=self.colors['danger'])
        self.measure_info.config(text="测量已停止")
        
        # 保存部分结果
        if self.current_result and self.current_result.values:
            self.history_results.append(self.current_result)
            self.current_result = None
        
        print("测量已停止")
    
    def finish_measurement(self):
        """完成测量"""
        if not self.current_result or not self.measurement_completed:
            return
        
        # 进入完成状态
        self.change_state(AppState.COMPLETING)
        
        # 计算最终平均值
        if self.current_result.adjusted_values and not self.current_result.average:
            self.current_result.average = sum(self.current_result.adjusted_values) / len(self.current_result.adjusted_values)
            self.current_result.end_time = datetime.now()
        
        # 保存结果
        self.history_results.append(self.current_result)
        
        # 显示最终结果
        error = self.current_result.get_error()
        if error is not None:
            error_text = f"误差: {abs(error):.1f}%"
            level = self.current_result.get_error_level()
            # 修复：从字典获取颜色
            color_key = f'error_{level}'
            color = self.colors.get(color_key, self.colors['text'])
            
            self.measure_info.config(text=f"测量完成，{error_text}")
            
            # 更新卡片显示误差
            self.update_result_card(show_error=True)
        
        # 完成测量
        result_copy = self.current_result
        self.current_result = None
        self.measurement_completed = False
        
        # 更新状态
        self.change_state(AppState.CONNECTED)
        self.speed_display.config(text="测量完成", fg=self.colors['success'])
        
        print(f"测量完成，共 {len(result_copy.values)} 次测量")
    
    # ==================== UI更新方法 ====================
    def update_progress(self, progress: float):
        """更新进度条"""
        self.progress_var.set(progress)
        self.progress_label.config(text=f"{progress:.1f}%")
    
    def create_result_card(self, config: MeasurementConfig):
        """创建结果卡片"""
        # 创建卡片框架
        card = tk.Frame(
            self.results_canvas,
            bg=self.colors['bg_card'],
            relief=tk.RAISED,
            bd=1,
            width=self.card_width,
            height=self.card_height
        )
        card.pack_propagate(False)  # 固定卡片大小
        
        # 存储卡片引用
        card_key = f"card_{config.speed_str}"
        
        # 计算卡片位置
        card_count = len(self.result_cards)
        
        # 计算每行可以放多少个卡片
        canvas_width = self.results_canvas.winfo_width()
        if canvas_width < 10:  # 如果画布宽度太小，默认放1个
            cards_per_row = 1
        else:
            cards_per_row = max(1, canvas_width // (self.card_width + self.card_padding))
        
        # 计算行和列
        row = card_count // cards_per_row
        col = card_count % cards_per_row
        
        # 计算坐标
        x = col * (self.card_width + self.card_padding) + (self.card_padding // 2)
        y = row * (self.card_height + self.card_padding) + (self.card_padding // 2)
        
        # 在画布上创建卡片窗口
        card_id = self.results_canvas.create_window(
            x, y,
            window=card,
            anchor="nw",
            width=self.card_width,
            height=self.card_height
        )
        
        # 存储卡片引用
        self.result_cards[card_key] = {
            'frame': card,
            'labels': {},
            'config': config,
            'canvas_id': card_id
        }
        
        # 标题
        title_label = tk.Label(
            card,
            text=config.speed_str,
            font=('微软雅黑', 11, 'bold'),
            bg=self.colors['bg_card'],
            fg=self.colors['accent']
        )
        title_label.pack(anchor='w', pady=(5, 5), padx=5)
        
        # 预期值
        expected_frame = tk.Frame(card, bg=self.colors['bg_card'])
        expected_frame.pack(fill=tk.X, pady=2, padx=5)
        
        tk.Label(
            expected_frame,
            text="预期:",
            font=('微软雅黑', 9),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        ).pack(side=tk.LEFT)
        
        expected_val = tk.Label(
            expected_frame,
            text=f"{config.expected_ms:.1f}ms",
            font=('Consolas', 9),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        )
        expected_val.pack(side=tk.RIGHT)
        self.result_cards[card_key]['labels']['expected'] = expected_val
        
        # 测得值
        measured_frame = tk.Frame(card, bg=self.colors['bg_card'])
        measured_frame.pack(fill=tk.X, pady=2, padx=5)
        
        tk.Label(
            measured_frame,
            text="测得:",
            font=('微软雅黑', 9),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        ).pack(side=tk.LEFT)
        
        measured_val = tk.Label(
            measured_frame,
            text="等待测量...",
            font=('Consolas', 9, 'bold'),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        )
        measured_val.pack(side=tk.RIGHT)
        self.result_cards[card_key]['labels']['measured'] = measured_val
        
        # 误差
        error_frame = tk.Frame(card, bg=self.colors['bg_card'])
        error_frame.pack(fill=tk.X, pady=2, padx=5)
        
        tk.Label(
            error_frame,
            text="误差:",
            font=('微软雅黑', 9),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        ).pack(side=tk.LEFT)
        
        error_val = tk.Label(
            error_frame,
            text="-",
            font=('Consolas', 9, 'bold'),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        )
        error_val.pack(side=tk.RIGHT)
        self.result_cards[card_key]['labels']['error'] = error_val
        
        # 更新画布的滚动区域
        self.update_canvas_scrollregion()
    
    def update_result_card(self, show_error=False):
        """更新结果卡片"""
        if not self.current_result:
            return
        
        card_id = f"card_{self.current_result.config.speed_str}"
        card_info = self.result_cards.get(card_id)
        
        if not card_info or 'labels' not in card_info:
            return
        
        labels = card_info['labels']
        
        # 更新测得值
        if self.current_result.adjusted_values:
            avg = sum(self.current_result.adjusted_values) / len(self.current_result.adjusted_values)
            labels['measured'].config(text=f"{avg:.3f}ms")
        
        # 更新误差
        if show_error and self.current_result.average is not None:
            error = self.current_result.get_error()
            if error is not None:
                error_text = f"{abs(error):.1f}%"
                level = self.current_result.get_error_level()
                # 修复：从字典获取颜色
                color_key = f'error_{level}'
                color = self.colors.get(color_key, self.colors['text'])
                labels['error'].config(text=error_text, fg=color)
    
    def update_canvas_scrollregion(self):
        """更新画布的滚动区域"""
        # 计算需要的总高度
        card_count = len(self.result_cards)
        canvas_width = self.results_canvas.winfo_width()
        
        if canvas_width < 10:
            canvas_width = 550  # 默认宽度
        
        cards_per_row = max(1, canvas_width // (self.card_width + self.card_padding))
        rows = (card_count + cards_per_row - 1) // cards_per_row  # 向上取整
        
        # 计算总高度
        total_height = rows * (self.card_height + self.card_padding) + self.card_padding
        
        # 更新滚动区域
        self.results_canvas.configure(
            scrollregion=(0, 0, canvas_width, total_height)
        )
    
    def clear_results(self):
        """清空所有测量结果"""
        if not messagebox.askyesno("确认", "确定要清空所有测量结果吗？"):
            return
        
        # 清除画布上的所有卡片
        for card_id, card_info in list(self.result_cards.items()):
            if 'canvas_id' in card_info:
                self.results_canvas.delete(card_info['canvas_id'])
            if 'frame' in card_info:
                card_info['frame'].destroy()
        
        # 清空卡片字典
        self.result_cards.clear()
        
        # 清空历史数据
        self.history_results.clear()
        
        # 重置实时显示
        self.speed_display.config(text="等待开始", fg=self.colors['accent'])
        self.measure_info.config(text="准备就绪")
        self.update_progress(0)
        
        # 更新滚动区域
        self.results_canvas.configure(scrollregion=(0, 0, 550, 0))
        
        print("已清空所有测量结果")
    
    def rearrange_cards(self):
        """重新排列所有卡片"""
        if not self.result_cards:
            return
        
        # 获取画布当前宽度
        canvas_width = self.results_canvas.winfo_width()
        if canvas_width < 10:
            return
        
        # 计算每行可以放多少个卡片
        cards_per_row = max(1, canvas_width // (self.card_width + self.card_padding))
        
        # 重新排列每个卡片
        card_items = list(self.result_cards.items())
        for index, (card_key, card_info) in enumerate(card_items):
            row = index // cards_per_row
            col = index % cards_per_row
            
            # 计算新坐标
            x = col * (self.card_width + self.card_padding) + (self.card_padding // 2)
            y = row * (self.card_height + self.card_padding) + (self.card_padding // 2)
            
            # 移动卡片到新位置
            if 'canvas_id' in card_info:
                self.results_canvas.coords(card_info['canvas_id'], x, y)
        
        # 更新滚动区域
        card_count = len(self.result_cards)
        rows = (card_count + cards_per_row - 1) // cards_per_row
        total_height = rows * (self.card_height + self.card_padding) + self.card_padding
        
        self.results_canvas.configure(
            scrollregion=(0, 0, canvas_width, total_height)
        )
    
    # ==================== GUI创建 ====================
    def create_gui(self):
        """创建GUI界面"""
        # 顶部标题
        header_frame = tk.Frame(self.root, bg=self.colors['dark'])
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
        
        title_label = tk.Label(
            header_frame,
            text="📷 快门测量诊断系统",
            font=('微软雅黑', 20, 'bold'),
            bg=self.colors['dark'],
            fg=self.colors['accent']
        )
        title_label.pack()
        
        # 主内容区域
        main_container = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=self.colors['dark'], sashwidth=4)
        main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # 左面板 - 控制区
        self.create_left_panel(main_container)
        
        # 右面板 - 结果显示区
        self.create_right_panel(main_container)
        
        # 状态栏
        self.create_status_bar()
    
    def create_left_panel(self, parent):
        """创建左控制面板"""
        panel = tk.Frame(self.root, bg=self.colors['bg_card'], bd=1, relief=tk.RAISED)
        parent.add(panel, width=400)
        
        # 串口控制区域
        serial_frame = self.create_label_frame(panel, "🔌 串口控制", padx=15, pady=10)
        serial_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        # 端口选择
        self.create_port_selection(serial_frame)
        
        # 连接/断开按钮
        self.create_connection_buttons(serial_frame)
        
        # 验证按钮
        self.verify_btn = tk.Button(
            serial_frame,
            text="🔧 验证设备",
            command=self.verify_device,
            bg=self.button_colors['verify'],
            fg='white',
            font=('微软雅黑', 10),
            padx=20,
            cursor='hand2',
            relief=tk.RAISED,
            state='disabled'
        )
        self.verify_btn.pack(fill=tk.X, pady=5)
        
        # 状态显示
        self.create_status_display(serial_frame)
        
        # 测量控制区域
        measure_frame = self.create_label_frame(panel, "🎯 测量控制", padx=15, pady=10)
        measure_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # 快门速度输入
        self.create_speed_input(measure_frame)
        
        # 测量次数
        self.create_count_input(measure_frame)
        
        # 开始/停止按钮
        self.create_measurement_buttons(measure_frame)
        
        # 清空结果按钮
        self.create_clear_button(panel)
    
    def create_right_panel(self, parent):
        """创建右结果面板"""
        panel = tk.Frame(self.root, bg=self.colors['bg_card'], bd=1, relief=tk.RAISED)
        parent.add(panel, width=550)
        
        # 实时测量显示
        realtime_frame = self.create_label_frame(panel, "📊 实时测量", padx=15, pady=10)
        realtime_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        # 当前速度显示
        self.speed_display = tk.Label(
            realtime_frame,
            text="等待开始",
            font=('Consolas', 24, 'bold'),
            bg=self.colors['bg_card'],
            fg=self.colors['accent']
        )
        self.speed_display.pack(pady=10)
        
        # 测量信息
        self.measure_info = tk.Label(
            realtime_frame,
            text="准备就绪",
            font=('微软雅黑', 10),
            bg=self.colors['bg_card'],
            fg=self.colors['text_secondary']
        )
        self.measure_info.pack(pady=(0, 10))
        
        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            realtime_frame,
            variable=self.progress_var,
            maximum=100,
            length=300
        )
        self.progress_bar.pack(pady=5)
        
        self.progress_label = tk.Label(
            realtime_frame,
            text="0%",
            font=('微软雅黑', 9),
            bg=self.colors['bg_card'],
            fg=self.colors['text_secondary']
        )
        self.progress_label.pack()
        
        # 结果区域
        result_frame = self.create_label_frame(panel, "📈 测量结果", padx=15, pady=10)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 创建结果画布
        self.create_results_canvas(result_frame)
    
    def create_port_selection(self, parent):
        """创建端口选择组件"""
        frame = tk.Frame(parent, bg=self.colors['bg_card'])
        frame.pack(fill=tk.X, pady=5)
        
        self.port_combo = ttk.Combobox(
            frame,
            state="readonly",
            width=30,
            font=('微软雅黑', 10)
        )
        self.port_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        self.scan_btn = tk.Button(
            frame,
            text="扫描端口",
            command=self.scan_ports,
            bg=self.button_colors['scan'],
            fg='white',
            font=('微软雅黑', 10),
            padx=15,
            cursor='hand2',
            relief=tk.RAISED
        )
        self.scan_btn.pack(side=tk.RIGHT)
    
    def create_connection_buttons(self, parent):
        """创建连接/断开按钮"""
        frame = tk.Frame(parent, bg=self.colors['bg_card'])
        frame.pack(fill=tk.X, pady=5)
        
        self.connect_btn = tk.Button(
            frame,
            text="连接设备",
            command=self.connect_device,
            bg=self.button_colors['connect'],
            fg='white',
            font=('微软雅黑', 10, 'bold'),
            padx=20,
            cursor='hand2',
            relief=tk.RAISED,
            state='disabled'
        )
        self.connect_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        self.disconnect_btn = tk.Button(
            frame,
            text="断开连接",
            command=self.disconnect_device,
            bg=self.button_colors['disconnect'],
            fg='white',
            font=('微软雅黑', 10),
            padx=20,
            cursor='hand2',
            relief=tk.RAISED,
            state='disabled'
        )
        self.disconnect_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True)
    
    def create_status_display(self, parent):
        """创建状态显示"""
        frame = tk.Frame(parent, bg=self.colors['bg_card'])
        frame.pack(fill=tk.X, pady=5)
        
        # 状态指示灯
        self.status_indicator = tk.Canvas(
            frame,
            width=12,
            height=12,
            bg=self.colors['bg_card'],
            highlightthickness=0
        )
        self.status_indicator.pack(side=tk.LEFT, padx=(0, 10))
        self.status_circle = self.status_indicator.create_oval(2, 2, 10, 10, fill='#666666')
        
        # 状态文本
        self.status_label = tk.Label(
            frame,
            text="未连接",
            font=('微软雅黑', 10),
            bg=self.colors['bg_card'],
            fg=self.colors['text_secondary']
        )
        self.status_label.pack(side=tk.LEFT)
        
        # 验证状态
        self.verify_status = tk.Label(
            frame,
            text="○ 未验证",
            font=('微软雅黑', 10),
            bg=self.colors['bg_card'],
            fg='#666666'
        )
        self.verify_status.pack(side=tk.RIGHT)
    
    def create_speed_input(self, parent):
        """创建快门速度输入"""
        frame = tk.Frame(parent, bg=self.colors['bg_card'])
        frame.pack(fill=tk.X, pady=5)
        
        tk.Label(
            frame,
            text="快门速度:",
            font=('微软雅黑', 10),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        ).pack(side=tk.LEFT)
        
        self.speed_var = tk.StringVar(value="1/125")
        self.speed_entry = tk.Entry(
            frame,
            textvariable=self.speed_var,
            font=('微软雅黑', 10),
            bg='white',
            fg='black',
            width=15
        )
        self.speed_entry.pack(side=tk.RIGHT, padx=(10, 0))
    
    def create_count_input(self, parent):
        """创建测量次数输入"""
        frame = tk.Frame(parent, bg=self.colors['bg_card'])
        frame.pack(fill=tk.X, pady=5)
        
        tk.Label(
            frame,
            text="测量次数:",
            font=('微软雅黑', 10),
            bg=self.colors['bg_card'],
            fg=self.colors['text']
        ).pack(side=tk.LEFT)
        
        self.count_var = tk.StringVar(value="3")
        self.count_spin = tk.Spinbox(
            frame,
            from_=1,
            to=10,
            textvariable=self.count_var,
            font=('微软雅黑', 10),
            bg='white',
            fg='black',
            width=8
        )
        self.count_spin.pack(side=tk.RIGHT, padx=(10, 0))
    
    def create_measurement_buttons(self, parent):
        """创建测量控制按钮"""
        frame = tk.Frame(parent, bg=self.colors['bg_card'])
        frame.pack(fill=tk.X, pady=(10, 0))
        
        self.start_btn = tk.Button(
            frame,
            text="▶ 开始测量",
            command=self.start_measurement,
            bg=self.button_colors['start'],
            fg='white',
            font=('微软雅黑', 10, 'bold'),
            cursor='hand2',
            relief=tk.RAISED,
            state='disabled'
        )
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.stop_btn = tk.Button(
            frame,
            text="■ 停止",
            command=self.stop_measurement,
            bg=self.button_colors['stop'],
            fg='white',
            font=('微软雅黑', 10),
            cursor='hand2',
            relief=tk.RAISED,
            state='disabled'
        )
        self.stop_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0))
    
    def create_clear_button(self, parent):
        """创建清空结果按钮"""
        clear_frame = self.create_label_frame(parent, "🔄 结果管理", padx=15, pady=10)
        clear_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.clear_btn = tk.Button(
            clear_frame,
            text="🗑️ 清空所有结果",
            command=self.clear_results,
            bg='#dc3545',  # 使用危险颜色
            fg='white',
            font=('微软雅黑', 10),
            padx=20,
            cursor='hand2',
            relief=tk.RAISED
        )
        self.clear_btn.pack(fill=tk.X, pady=5)
        
        # 添加提示文本
        tk.Label(
            clear_frame,
            text="注意：此操作将删除所有历史测量数据",
            font=('微软雅黑', 8),
            bg=self.colors['bg_card'],
            fg=self.colors['text_secondary']
        ).pack(pady=(0, 5))
    
    def create_results_canvas(self, parent):
        """创建结果画布"""
        frame = tk.Frame(parent, bg=self.colors['bg_card'])
        frame.pack(fill=tk.BOTH, expand=True)
        
        # 画布
        self.results_canvas = tk.Canvas(
            frame,
            bg=self.colors['bg_card'],
            highlightthickness=0
        )
        
        # 滚动条
        scrollbar = ttk.Scrollbar(
            frame,
            orient=tk.VERTICAL,
            command=self.results_canvas.yview
        )
        
        # 配置滚动
        self.results_canvas.configure(yscrollcommand=scrollbar.set)
        
        # 鼠标滚轮
        def on_mousewheel(event):
            self.results_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        self.results_canvas.bind_all("<MouseWheel>", on_mousewheel)
        
        # 窗口大小变化事件
        def on_canvas_resize(event):
            # 重新布局所有卡片
            self.rearrange_cards()
        
        self.results_canvas.bind('<Configure>', on_canvas_resize)
        
        # 布局
        self.results_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    def create_status_bar(self):
        """创建状态栏"""
        status_bar = tk.Frame(self.root, bg=self.colors['primary'], height=25)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        
        tk.Label(
            status_bar,
            text="快门测量诊断系统 v1.0",
            bg=self.colors['primary'],
            fg=self.colors['text_secondary'],
            font=('微软雅黑', 9)
        ).pack(side=tk.LEFT, padx=10)
    
    # ==================== 辅助方法 ====================
    def create_label_frame(self, parent, text, **kwargs):
        """创建标签框架"""
        return tk.LabelFrame(
            parent,
            text=text,
            font=('微软雅黑', 11, 'bold'),
            bg=self.colors['bg_card'],
            fg=self.colors['accent'],
            **kwargs
        )

# ==================== 主程序入口 ====================
def main():
    root = tk.Tk()
    app = ShutterMeasurementApp(root)
    
    # 窗口关闭事件
    def on_closing():
        # 确保断开连接
        if app.device.port and app.device.is_open:
            app.device.port.close()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()