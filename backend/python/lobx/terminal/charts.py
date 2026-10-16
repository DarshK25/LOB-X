"""Terminal chart renderers for LOB-X."""
from __future__ import annotations

from collections import deque
from typing import Deque

from rich.text import Text


class TerminalChart:
    """
    Renders time-series data as terminal charts.
    
    Uses block characters for smooth rendering without external dependencies.
    """
    
    def __init__(self, height: int = 10, width: int = 60):
        self.height = height
        self.width = width
    
    def render_line(
        self,
        data: Deque[tuple[int, float]] | list[tuple[int, float]],
        title: str = "",
        color: str = "cyan",
        show_value: bool = True,
    ) -> Text:
        """Render line chart from (timestamp, value) pairs."""
        if not data or len(data) < 2:
            text = Text()
            text.append(f"\n{title}\n" if title else "\n", style="bold")
            text.append("  Waiting for data...\n", style="dim")
            return text
        
        # Take last N points to fit width
        points = list(data)[-self.width:]
        values = [v for _, v in points]
        
        if not values:
            return Text("No data")
        
        min_val = min(values)
        max_val = max(values)
        span = max_val - min_val or 1.0
        
        # Create grid
        H = self.height
        W = len(values)
        
        # Map values to rows (using 4 sub-levels per row for smoothness)
        total_sublevels = H * 4
        blocks = [" ", "▁", "▄", "▀", "█"]
        
        grid = [[" " for _ in range(W)] for _ in range(H)]
        colors = [["dim" for _ in range(W)] for _ in range(H)]
        
        for col, val in enumerate(values):
            frac = (val - min_val) / span
            sub = int(frac * (total_sublevels - 1))
            row_idx = H - 1 - (sub // 4)
            row_idx = max(0, min(H - 1, row_idx))
            ch = blocks[(sub % 4) + 1]
            grid[row_idx][col] = ch
            
            # Color based on trend
            if col > 0:
                is_up = val >= values[col - 1]
                colors[row_idx][col] = f"bold {color}" if color != "cyan" else ("green" if is_up else "red")
            else:
                colors[row_idx][col] = f"bold {color}"
        
        # Build output
        text = Text()
        if title:
            text.append(f"{title}\n", style="bold")
        
        # Y-axis labels
        y_labels = [min_val, (min_val + max_val) / 2, max_val]
        y_rows = [H - 1, H // 2, 0]
        
        for r in range(H):
            # Y-axis label
            if r in y_rows:
                idx = y_rows.index(r)
                text.append(f"{y_labels[idx]:>8.2f} ", style="dim white")
            else:
                text.append(" " * 9, style="")
            
            text.append("│", style="dim")
            
            for c in range(W):
                text.append(grid[r][c], style=colors[r][c])
            text.append("\n")
        
        # X-axis
        text.append(" " * 9 + "└" + "─" * W + "\n", style="dim")
        
        if show_value:
            latest = values[-1]
            text.append(f"  Latest: {latest:,.2f}", style=f"bold {color}")
        
        return text
    
    def render_dual_line(
        self,
        data1: Deque[tuple[int, float]] | list[tuple[int, float]],
        data2: Deque[tuple[int, float]] | list[tuple[int, float]],
        label1: str = "Series 1",
        label2: str = "Series 2",
        color1: str = "green",
        color2: str = "blue",
    ) -> Text:
        """Render two overlapping line series."""
        if not data1 or not data2:
            return Text("Waiting for data...")
        
        # Align data by taking common length
        n = min(len(data1), len(data2), self.width)
        points1 = list(data1)[-n:]
        points2 = list(data2)[-n:]
        
        values1 = [v for _, v in points1]
        values2 = [v for _, v in points2]
        
        if not values1 or not values2:
            return Text("No data")
        
        # Combined min/max for same scale
        all_vals = values1 + values2
        min_val = min(all_vals)
        max_val = max(all_vals)
        span = max_val - min_val or 1.0
        
        H = self.height
        W = len(values1)
        
        grid = [[" " for _ in range(W)] for _ in range(H)]
        
        # Plot both series
        for col in range(W):
            for vals, color in [(values1, color1), (values2, color2)]:
                if col >= len(vals):
                    continue
                val = vals[col]
                frac = (val - min_val) / span
                row_idx = H - 1 - int(frac * (H - 1))
                row_idx = max(0, min(H - 1, row_idx))
                
                # Use different characters
                if color == color1:
                    grid[row_idx][col] = "█"
                else:
                    if grid[row_idx][col] == "█":
                        grid[row_idx][col] = "▓"  # Overlap
                    else:
                        grid[row_idx][col] = "░"
        
        text = Text()
        text.append(f"  {label1} ", style=f"bold {color1}")
        text.append(f"  {label2}\n", style=f"bold {color2}")
        
        for r in range(H):
            text.append(" " * 9 + "│", style="dim")
            for c in range(W):
                ch = grid[r][c]
                if ch == "█":
                    text.append(ch, style=f"bold {color1}")
                elif ch == "░":
                    text.append(ch, style=f"bold {color2}")
                elif ch == "▓":
                    text.append(ch, style="bold magenta")
                else:
                    text.append(ch)
            text.append("\n")
        
        text.append(" " * 9 + "└" + "─" * W + "\n", style="dim")
        
        return text


def render_sparkline(values: list[float], width: int = 40) -> str:
    """Render a compact sparkline."""
    if not values or len(values) < 2:
        return "─" * width
    
    # Resample to width
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values + [values[-1]] * (width - len(values))
    
    min_val = min(sampled)
    max_val = max(sampled)
    span = max_val - min_val or 1.0
    
    # 8 levels of block characters
    chars = " ▁▂▃▄▅▆▇█"
    result = ""
    for val in sampled:
        frac = (val - min_val) / span
        idx = int(frac * (len(chars) - 1))
        result += chars[idx]
    
    return result
