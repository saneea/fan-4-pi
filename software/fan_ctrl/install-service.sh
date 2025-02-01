mkdir /etc/fan-pwm-control
cp ./config.conf /etc/fan-pwm-control/config.conf
cp ./fan_ctrl.py /usr/bin/fan_ctrl.py
cp ./fanctrl.service /lib/systemd/system/fanctrl.service
systemctl enable fanctrl
systemctl start fanctrl
