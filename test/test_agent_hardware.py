"""Hardware-free checks for the explicit agent-control mode."""
import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def load_pidog_with_hardware_stubs():
    robot_hat = types.ModuleType('robot_hat')
    robot_hat.Robot = MagicMock(name='Robot')
    robot_hat.Pin = MagicMock(name='Pin')
    robot_hat.Ultrasonic = MagicMock(name='Ultrasonic')
    robot_hat.Music = MagicMock(name='Music')
    robot_hat.I2C = MagicMock(name='I2C')
    robot_hat.fileDB = MagicMock(name='fileDB')
    robot_hat.utils = types.SimpleNamespace(reset_mcu=MagicMock(name='reset_mcu'),
                                            run_command=MagicMock(return_value=(0, '')))
    robot_hat.__path__ = []
    robot_hat_pin = types.ModuleType('robot_hat.pin')
    robot_hat_pin.Pin = robot_hat.Pin
    smbus = types.ModuleType('smbus')
    smbus.SMBus = MagicMock(name='SMBus')
    spidev = types.ModuleType('spidev')
    spidev.SpiDev = MagicMock(name='SpiDev')
    lgpio = types.ModuleType('lgpio')
    readchar = types.ModuleType('readchar')

    stubs = {'robot_hat': robot_hat, 'robot_hat.pin': robot_hat_pin,
             'smbus': smbus, 'spidev': spidev, 'lgpio': lgpio,
             'readchar': readchar}
    patcher = patch.dict(sys.modules, stubs)
    patcher.start()
    importlib.invalidate_caches()
    module = importlib.import_module('pidog.pidog')
    return module, patcher


class AgentHardwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.hardware_stubs = load_pidog_with_hardware_stubs()

    @classmethod
    def tearDownClass(cls):
        cls.hardware_stubs.stop()

    def make_agent_dog(self):
        module = self.module
        module.Robot.reset_mock()
        module.utils.reset_mcu.reset_mock()
        with patch.object(module, 'Sh3001'), \
                patch.object(module, 'RGBStrip') as rgb_cls, \
                patch.object(module, 'DualTouch'), \
                patch.object(module, 'SoundDirection'), \
                patch.object(module, 'Music'), \
                patch.object(module.Pidog, 'action_threads_start'), \
                patch.object(module.Pidog, 'sensory_process_start'):
            dog = module.Pidog(agent_control=True)
        return dog, rgb_cls

    def test_agent_init_skips_reset_and_servo_initialization(self):
        _, rgb_cls = self.make_agent_dog()
        self.module.utils.reset_mcu.assert_not_called()
        self.assertEqual(self.module.Robot.call_count, 3)
        for call in self.module.Robot.call_args_list:
            self.assertEqual(call.kwargs['init_order'], [])
        rgb_cls.return_value.set_mode.assert_called_once_with('monochromatic', 'black')

    def test_agent_sound_apis_raise_before_playback(self):
        dog, _ = self.make_agent_dog()
        for method in (dog.speak, dog.speak_block):
            with self.assertRaisesRegex(RuntimeError, 'disabled in agent control mode'):
                method('single_bark_1')
        dog.music.sound_play.assert_not_called()
        dog.music.sound_play_threading.assert_not_called()

    def test_agent_close_clears_queues_without_parking(self):
        dog, _ = self.make_agent_dog()
        dog.legs_action_buffer.append([1] * 8)
        dog.head_action_buffer.append([1] * 3)
        dog.tail_action_buffer.append([1])
        dog.stop_and_lie = MagicMock(side_effect=AssertionError('must not park'))
        dog.legs_thread = MagicMock()
        dog.head_thread = MagicMock()
        dog.tail_thread = MagicMock()
        dog.rgb_thread = MagicMock()
        dog.thread_list = ['legs', 'head', 'tail', 'rgb']
        dog.rgb_thread_run = True
        dog.rgb_strip.close = MagicMock()

        dog.close()

        dog.stop_and_lie.assert_not_called()
        self.assertEqual(dog.legs_action_buffer, [])
        self.assertEqual(dog.head_action_buffer, [])
        self.assertEqual(dog.tail_action_buffer, [])
        self.assertTrue(dog.exit_flag)
        self.assertTrue(dog._sensory_exit_flag)
        self.assertFalse(dog.rgb_thread_run)
        dog.legs_thread.join.assert_called_once_with()
        dog.rgb_strip.close.assert_called_once_with()

    def test_action_flow_standby_does_not_run_ambient_actions(self):
        from pidog.action_flow import ActionFlow, ActionStatus

        dog = MagicMock()
        flow = ActionFlow(dog)
        flow.thread_action_state = ActionStatus.STANDBY
        flow.thread_running = True
        flow.run = MagicMock()

        with patch('pidog.action_flow.time.sleep', side_effect=[None, None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                flow.action_handler()

        flow.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
