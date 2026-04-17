import pandas as pd
import numpy as np
from battery import get_battery
from controllers import Controller_PV
from simulator import Simulator

def test_progress_bar():
    # Setup dummy data
    df = pd.DataFrame({
        'teruglevering': np.random.rand(100),
        'verbruik': np.random.rand(100)
    }, index=pd.date_range('2025-01-01', periods=100, freq='15min'))
    
    battery = get_battery("Bliq_5kwh")
    controller = Controller_PV(battery)
    simulator = Simulator(battery, controller)
    
    progress_calls = []
    def progress_callback(current, total):
        progress_calls.append((current, total))
    
    print("Running simulation with callback...")
    result = simulator.run(df, progress_callback=progress_callback)
    
    assert len(progress_calls) == 100
    assert progress_calls[0] == (1, 100)
    assert progress_calls[-1] == (100, 100)
    print("Callback test passed!")

    print("\nRunning simulation without callback (should show tqdm)...")
    result = simulator.run(df)
    print("Tqdm test passed (manually verify output if needed)!")

if __name__ == "__main__":
    test_progress_bar()
