# CARE ROBOT

## Description

A simulation of a service robot in an elder care home. The simulation environment should demonstrate different use cases. This includes a suitable robot, intelligence, interaction to different software parts like a mobile application or a server.

## Contributing

make branch with the name format "feature_username_featurename" . After implementing the necessary task put an Merge Request to the main branch, which will be reviewed and merged by the owner.
Peer reviews are expected when someone is making a merge request.

# How to start

## Requirements

Install below tools to run the project:

- Cyberbotics Webots: "https://cyberbotics.com/"
- Any IDE preferable VS Code: "https://code.visualstudio.com/download"
- Mosquitto broker: "https://mosquitto.org/download/" => after installation, the MQTT broker should run as process in your OS
- Python 3: "https://www.python.org/downloads/"

## Start the 3D simulation

- Open the "Alia_Delivery.wbt" simulation file in Webots.
- The file exist on path "webots/worlds/Alia_Delivery"

## Run the Web Application

- Run VS Code IDE and open the web_app folder in it.

## Install Dependencies

- Open bash or command prompt and execute the command: `pip install -r requirements.txt`

## Run the web server

- Run the project by executing the command: `python app.py`
- Open browser and access localhost on port 5050: [http://127.0.0.1:5050](http://127.0.0.1:5050)

## Interact With Web Interface

- Use the dropdown to select a room and send a command.
- The robot should listen to the command and start moving to the selected room.

With the help of MQTT Explorer or other debug tools you can always monitor the messages on your MQTT broker.
