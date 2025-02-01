systemctl stop fanctrl
systemctl disable fanctrl
rm /lib/systemd/system/fanctrl.service
rm /usr/bin/fan_ctrl.py
rm /etc/fan-pwm-control/config.conf
rmdir /etc/fan-pwm-control
