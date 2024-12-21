# -*- coding: utf-8 -*-

import configparser
import logging
import os
import time
from abc import ABC, abstractmethod

ALLOWED_LOG_LEVELS = [
    logging.CRITICAL,
    logging.ERROR,
    logging.WARNING,
    logging.INFO,
    logging.DEBUG
]
ALLOWED_LOG_LEVELS_NAMES = list(map(logging.getLevelName, ALLOWED_LOG_LEVELS))

GPIO_DRIVER_FAKE = 'fake'
GPIO_DRIVER_RPI = 'rpi'

SECTION_MAIN = 'Main'
SECTION_GPIO = 'GPIO'
SECTION_OTHER = 'Other'
SECTION_DISABLE_FAN = "Disable fan"
SECTION_MIM_FAN_SPEED = "Min fan speed"
SECTION_MAX_FAN_SPEED = "Max fan speed"

log_level = os.getenv('LOG_LEVEL', logging.getLevelName(logging.WARNING))
gpio_driver_name = os.getenv('GPIO_DRIVER', GPIO_DRIVER_RPI)
config_file_name = os.getenv('CONFIG_FILE', "/etc/fan-pwm-control/config.conf")

if log_level not in ALLOWED_LOG_LEVELS_NAMES:
    raise ValueError(f"Unknown $LOG_LEVEL env var '{log_level}'. Use one of {ALLOWED_LOG_LEVELS_NAMES}")

logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.getLevelName(log_level))


class TempFanPoint:
    def __init__(self, temp, fan):
        self.temp = temp
        self.fan = fan


class IGpioDriver(ABC):
    @abstractmethod
    def setup(self, fan_pin, pwm_freq, start_speed):
        pass

    @abstractmethod
    def cleanup(self):
        pass

    @abstractmethod
    def set_fan_speed(self, fan_speed):
        pass


class FakeGpioDriver(IGpioDriver):

    def setup(self, fan_pin, pwm_freq, start_speed):
        pass

    def cleanup(self):
        pass

    def set_fan_speed(self, fan_speed):
        pass


class IGpioFacade(ABC):
    @abstractmethod
    def setup(self, fan_pin, pwm_freq, start_speed):
        pass

    @abstractmethod
    def cleanup(self):
        pass

    @abstractmethod
    def get_fan_speed(self):
        pass

    @abstractmethod
    def set_fan_speed(self, fan_speed):
        pass


class GpioFacadeBasedOnGpioDriver(IGpioFacade):
    def __init__(self, driver):
        self.driver = driver
        self.fan_speed = None

    def setup(self, fan_pin, pwm_freq, start_speed):
        self.driver.setup(fan_pin, pwm_freq, start_speed)
        self.fan_speed = start_speed

    def cleanup(self):
        self.driver.cleanup()

    def get_fan_speed(self):
        return self.fan_speed

    def set_fan_speed(self, fan_speed):
        self.driver.set_fan_speed(fan_speed)
        self.fan_speed = fan_speed


class LoggerWrapperGpioFacade(IGpioFacade):

    def __init__(self, orig_gpio_facade):
        self.orig_gpio_facade = orig_gpio_facade

    @staticmethod
    def log(message):
        logger.info(f"[gpio] {message}")

    def setup(self, fan_pin, pwm_freq, start_speed):
        self.log(f"init(fan_pin={fan_pin}, pwm_freq={pwm_freq}, start_speed={start_speed})")
        self.orig_gpio_facade.setup(fan_pin, pwm_freq, start_speed)

    def cleanup(self):
        self.log("cleanup()")
        self.orig_gpio_facade.cleanup()

    def get_fan_speed(self):
        return self.orig_gpio_facade.get_fan_speed()

    def set_fan_speed(self, fan_speed):
        self.log(f"set_fan_speed({fan_speed})")
        self.orig_gpio_facade.set_fan_speed(fan_speed)


class ThresholdWrapperGpioFacade(IGpioFacade):

    def __init__(self, orig_gpio_facade, speed_change_threshold):
        self.orig_gpio_facade = orig_gpio_facade
        self.speed_change_threshold = speed_change_threshold

    def setup(self, fan_pin, pwm_freq, start_speed):
        self.orig_gpio_facade.setup(fan_pin, pwm_freq, start_speed)

    def cleanup(self):
        self.orig_gpio_facade.cleanup()

    def get_fan_speed(self):
        return self.orig_gpio_facade.get_fan_speed()

    def set_fan_speed(self, fan_speed):
        current_and_desired_speed_diff = abs(self.get_fan_speed() - fan_speed)
        if current_and_desired_speed_diff > self.speed_change_threshold:
            self.orig_gpio_facade.set_fan_speed(fan_speed)
        else:
            logger.debug(
                f"Skip fan speed changing because diff between current and desired speed is "
                f"{current_and_desired_speed_diff} but threshold is {self.speed_change_threshold}"
            )


def create_temp_fan_point(config, section_name):
    return TempFanPoint(
        config.getint(section=section_name, option="temp"),
        config.getint(section=section_name, option="fan")
    )


def calc_fan_for_temp(min_point, max_point, temp):
    min_temp = float(min_point.temp)
    min_fan = float(min_point.fan)
    max_temp = float(max_point.temp)
    max_fan = float(max_point.fan)
    return max_fan + (min_fan - max_fan) / (min_temp - max_temp) * (temp - max_temp)


class FanPwmControlConfig:
    def __init__(
            self,
            thermal_file,
            fan_pin,
            pwm_freq,
            speed_change_threshold,
            refresh_wait_time,
            disable_fan_temp,
            min_point,
            max_point
    ):
        if disable_fan_temp >= min_point.temp:
            raise ValueError(f"'{SECTION_DISABLE_FAN}/temp' value must be less than '{SECTION_MIM_FAN_SPEED}/temp'")

        if min_point.temp >= max_point.temp:
            raise ValueError(f"'{SECTION_MIM_FAN_SPEED}/temp' value must be less than '{SECTION_MAX_FAN_SPEED}/temp'")

        if min_point.fan >= max_point.fan:
            raise ValueError(
                f"'{SECTION_MIM_FAN_SPEED}/fan' value must be"
                f" less than (or equals to) '{SECTION_MAX_FAN_SPEED}/fan'"
            )

        self.thermal_file = thermal_file
        self.fan_pin = fan_pin
        self.pwm_freq = pwm_freq
        self.speed_change_threshold = speed_change_threshold
        self.refresh_wait_time = refresh_wait_time
        self.disable_fan_temp = disable_fan_temp
        self.min_point = min_point
        self.max_point = max_point


def create_fan_pwm_control_config():
    config_parser = configparser.ConfigParser()
    config_parser.read(config_file_name)

    return FanPwmControlConfig(
        thermal_file=config_parser.get(section=SECTION_MAIN, option='thermal_file'),
        fan_pin=config_parser.getint(section=SECTION_GPIO, option='fan_pin'),
        pwm_freq=config_parser.getint(section=SECTION_GPIO, option='pwm_freq'),
        speed_change_threshold=config_parser.getint(section=SECTION_OTHER, option='speed_change_threshold'),
        refresh_wait_time=config_parser.getint(section=SECTION_OTHER, option='refresh_wait_time'),
        disable_fan_temp=config_parser.getint(section=SECTION_DISABLE_FAN, option="temp"),
        min_point=create_temp_fan_point(config_parser, SECTION_MIM_FAN_SPEED),
        max_point=create_temp_fan_point(config_parser, SECTION_MAX_FAN_SPEED)
    )


if gpio_driver_name == GPIO_DRIVER_FAKE:
    gpio_driver = FakeGpioDriver()
elif gpio_driver_name == GPIO_DRIVER_RPI:
    import RPi.GPIO as GPIO


    class RpiGpioDriver(IGpioDriver):
        def setup(self, fan_pin, pwm_freq, start_speed):
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(fan_pin, GPIO.OUT, initial=GPIO.LOW)
            self.fan = GPIO.PWM(fan_pin, pwm_freq)
            self.fan.start(start_speed)

        def cleanup(self):
            GPIO.cleanup()

        def set_fan_speed(self, fan_speed):
            self.fan.ChangeDutyCycle(fan_speed)


    gpio_driver = RpiGpioDriver()
else:
    raise ValueError(f"Unknown gpio driver was specified: {gpio_driver_name}")


def get_current_temp(thermal_file_name):
    thermal_file = open(thermal_file_name, "r")
    try:
        return float(thermal_file.read()) / 1000
    finally:
        thermal_file.close()


def update_fan_speed(gpio_facade, fan_pwm_control_config):
    cpu_temp = get_current_temp(fan_pwm_control_config.thermal_file)
    logger.debug(f"Current temp = {cpu_temp}")
    if cpu_temp <= fan_pwm_control_config.disable_fan_temp:
        logger.debug("Temp is too low")
        logger.debug("Let's disable fan")
        gpio_facade.set_fan_speed(0)
    elif cpu_temp <= fan_pwm_control_config.min_point.temp:
        fan_enabled = gpio_facade.get_fan_speed() > 0
        fan_status_str = "enabled" if fan_enabled else "disabled"
        logger.debug("Temp is within 'sticky' interval. Fan is " + fan_status_str)
        logger.debug("Let's keep it " + fan_status_str)
        if fan_enabled:
            logger.debug(f"... but set fan speed to min ({fan_pwm_control_config.min_point.fan})")
            gpio_facade.set_fan_speed(fan_pwm_control_config.min_point.fan)
    elif cpu_temp <= fan_pwm_control_config.max_point.temp:
        logger.debug("Temp is within min..max interval")
        fan_interpol = calc_fan_for_temp(fan_pwm_control_config.min_point, fan_pwm_control_config.max_point, cpu_temp)
        logger.debug(f"Let's set fan speed to calculated {fan_interpol}")
        gpio_facade.set_fan_speed(fan_interpol)
    else:
        logger.debug("Temp is too high")
        logger.debug(f"Let's set fan speed to max ({fan_pwm_control_config.max_point.fan})")
        gpio_facade.set_fan_speed(fan_pwm_control_config.max_point.fan)

    time.sleep(fan_pwm_control_config.refresh_wait_time)


def main():
    fan_pwm_control_config = create_fan_pwm_control_config()

    gpio_facade = GpioFacadeBasedOnGpioDriver(gpio_driver)
    gpio_facade = LoggerWrapperGpioFacade(gpio_facade)
    gpio_facade = ThresholdWrapperGpioFacade(gpio_facade, fan_pwm_control_config.speed_change_threshold)

    gpio_facade.setup(fan_pwm_control_config.fan_pin, fan_pwm_control_config.pwm_freq, 0)
    try:
        while True:
            update_fan_speed(gpio_facade, fan_pwm_control_config)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt exception")
    finally:
        gpio_facade.cleanup()


main()
