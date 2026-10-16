"""Tests for probabilistic queue position model."""
import pytest

from lobx.execution.queue_model import (
    PowerProbFunc3,
    ProbabilisticQueueModel,
    QueuePosition,
)


class TestPowerProbFunc3:
    """Test PowerProb3 probability function."""
    
    def test_formula_correctness(self):
        """Verify prob = 1 - (front/(front+back))^n."""
        prob = PowerProbFunc3(n=3.0)
        
        # Equal queue: 50% at back, probability ~87.5%
        result = prob.prob(10.0, 10.0)  # front, back
        expected = 1.0 - (10.0 / 20.0) ** 3.0  # = 1 - 0.125 = 0.875
        assert abs(result - expected) < 0.001
    
    def test_empty_front(self):
        """Front queue exhausted = 100% probability."""
        prob = PowerProbFunc3(n=3.0)
        result = prob.prob(0.0, 10.0)
        assert result == 1.0
    
    def test_empty_back(self):
        """No back queue = 0% probability."""
        prob = PowerProbFunc3(n=3.0)
        result = prob.prob(10.0, 0.0)
        assert result == 0.0
    
    def test_n_effect(self):
        """Higher n = more conservative (lower probability)."""
        # At front=back, prob depends on n
        result_n1 = PowerProbFunc3(n=1.0).prob(10.0, 10.0)
        result_n3 = PowerProbFunc3(n=3.0).prob(10.0, 10.0)
        result_n5 = PowerProbFunc3(n=5.0).prob(10.0, 10.0)
        
        # All should be 0.5 for n=1, higher for larger n
        assert abs(result_n1 - 0.5) < 0.001
        assert result_n3 > result_n1
        assert result_n5 > result_n3


class TestProbabilisticQueueModel:
    """Test probabilistic queue model."""
    
    def test_initialization(self):
        """Order joins back of queue."""
        model = ProbabilisticQueueModel()
        pos = model.initialize_position(level_qty=5.0)
        
        assert pos.front_qty == 5.0  # All quantity ahead
        assert pos.cum_trade_qty == 0.0
    
    def test_trade_consumption(self):
        """Trades unconditionally reduce front queue."""
        model = ProbabilisticQueueModel()
        pos = QueuePosition(front_qty=5.0, cum_trade_qty=0.0)
        
        model.on_trade(pos, trade_qty=2.0)
        
        assert pos.front_qty == 3.0
        assert pos.cum_trade_qty == 2.0
    
    def test_depth_increase(self):
        """Depth increase doesn't advance queue."""
        model = ProbabilisticQueueModel()
        pos = QueuePosition(front_qty=5.0, cum_trade_qty=0.0)
        
        model.on_depth_change(pos, prev_level_qty=10.0, new_level_qty=12.0)
        
        # Front unchanged (new orders join behind)
        assert pos.front_qty == 5.0
        assert pos.cum_trade_qty == 0.0
    
    def test_depth_decrease(self):
        """Depth decrease advances queue probabilistically."""
        model = ProbabilisticQueueModel(PowerProbFunc3(n=3.0))
        pos = QueuePosition(front_qty=5.0, cum_trade_qty=0.0)
        
        # Level decreases from 10 to 8 (2 qty removed)
        model.on_depth_change(pos, prev_level_qty=10.0, new_level_qty=8.0)
        
        # Front should decrease (deterministic expected position)
        assert pos.front_qty < 5.0
        assert pos.cum_trade_qty == 0.0
    
    def test_trade_accounting(self):
        """Trades are not double-counted in depth updates."""
        model = ProbabilisticQueueModel()
        pos = QueuePosition(front_qty=5.0, cum_trade_qty=0.0)
        
        # Trade reduces queue
        model.on_trade(pos, trade_qty=2.0)
        assert pos.cum_trade_qty == 2.0
        
        # Depth update accounts for already-counted trades
        model.on_depth_change(pos, prev_level_qty=10.0, new_level_qty=8.0)
        
        # cum_trade_qty should be reset
        assert pos.cum_trade_qty == 0.0
    
    def test_fill_condition(self):
        """Order fills when front_qty <= 0."""
        model = ProbabilisticQueueModel()
        
        pos1 = QueuePosition(front_qty=0.1, cum_trade_qty=0.0)
        assert not model.is_filled(pos1)
        
        pos2 = QueuePosition(front_qty=0.0, cum_trade_qty=0.0)
        assert model.is_filled(pos2)
        
        pos3 = QueuePosition(front_qty=-0.1, cum_trade_qty=0.0)
        assert model.is_filled(pos3)
    
    def test_deterministic(self):
        """Model produces consistent results (no randomness)."""
        model1 = ProbabilisticQueueModel()
        model2 = ProbabilisticQueueModel()
        
        pos1 = QueuePosition(front_qty=5.0, cum_trade_qty=0.0)
        pos2 = QueuePosition(front_qty=5.0, cum_trade_qty=0.0)
        
        model1.on_depth_change(pos1, 10.0, 8.0)
        model2.on_depth_change(pos2, 10.0, 8.0)
        
        assert pos1.front_qty == pos2.front_qty


class TestQueueLifecycle:
    """Test complete order lifecycle."""
    
    def test_bid_fills_on_sell_aggressor(self):
        """Bid order fills when sell aggressor crosses."""
        model = ProbabilisticQueueModel()
        pos = model.initialize_position(level_qty=10.0)
        
        # Consume queue through trades
        for _ in range(15):  # Enough to exhaust 10 qty
            model.on_trade(pos, trade_qty=1.0)
        
        # Order should be filled
        assert model.is_filled(pos)
    
    def test_ask_fills_on_buy_aggressor(self):
        """Ask order fills when buy aggressor crosses."""
        # Same logic - side-agnostic
        model = ProbabilisticQueueModel()
        pos = model.initialize_position(level_qty=8.0)
        
        for _ in range(10):
            model.on_trade(pos, trade_qty=1.0)
        
        assert model.is_filled(pos)
    
    def test_partial_consumption(self):
        """Partial queue consumption advances position."""
        model = ProbabilisticQueueModel()
        pos = model.initialize_position(level_qty=10.0)
        
        # Consume half the queue
        for _ in range(5):
            model.on_trade(pos, trade_qty=1.0)
        
        # Should be closer but not filled
        assert pos.front_qty < 10.0
        assert pos.front_qty > 0.0
        assert not model.is_filled(pos)
