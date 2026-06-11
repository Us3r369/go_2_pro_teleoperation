import asyncio
import logging
import json
import sys
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC, SPORT_CMD

# Enable logging for debugging
logging.basicConfig(level=logging.FATAL)
    
async def main():
    try:
        # Choose a connection method (uncomment the correct one)
        # LocalAP: this machine is joined to the robot's own Wi-Fi hotspot (fixed IP 192.168.12.1).
        conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
        # LocalSTA: robot is on the same LAN as this machine (set its IP):
        # conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip="192.168.1.150")
        # conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, serialNumber="B42D2000XXXXXXXX")
        # conn = Go2WebRTCConnection(WebRTCConnectionMethod.Remote, serialNumber="B42D2000XXXXXXXX", username="email@gmail.com", password="pass")

        # Connect to the WebRTC service.
        await conn.connect()

        ####### NORMAL MODE ########
        print("Checking current motion mode...")

        # Get the current motion_switcher status
        response = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], 
            {"api_id": 1001}
        )

        if response['data']['header']['status']['code'] != 0:
            print("Could not read current motion mode; aborting.")
            return
        data = json.loads(response['data']['data'])
        current_motion_switcher_mode = data['name']
        print(f"Current motion mode: {current_motion_switcher_mode}")

        # Switch to "normal" mode if not already
        if current_motion_switcher_mode != "normal":
            print(f"Switching motion mode from {current_motion_switcher_mode} to 'normal'...")
            await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"], 
                {
                    "api_id": 1002,
                    "parameter": {"name": "normal"}
                }
            )
            await asyncio.sleep(5)  # Wait while it stands up

        await asyncio.sleep(1)

        # Perform a "Move Forward" movement
        print("Moving forward...")
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"], 
            {
                "api_id": SPORT_CMD["Move"],
                "parameter": {"x": 0.5, "y": 0, "z": 0}
            }
        )

        await asyncio.sleep(3)

        # Perform a "Move Backward" movement
        print("Moving backward...")
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"], 
            {
                "api_id": SPORT_CMD["Move"],
                "parameter": {"x": -0.5, "y": 0, "z": 0}
            }
        )

        await asyncio.sleep(3)

        # Perform a "Sit down" movement
        print("Sitting down...")
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {
                "api_id": SPORT_CMD["Sit"]
            }
        )

        await asyncio.sleep(3)  # Let the sit complete before the next posture

        # Perform a "Stand up" movement
        print("Standing up...")
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {
                "api_id": SPORT_CMD["StandUp"]
            }
        )

        await asyncio.sleep(3)

        # Perform a "Stand down" movement
        print("Standing down...")
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {
                "api_id": SPORT_CMD["StandDown"]
            }
        )

        # Give the final command time to execute, then disconnect.
        await asyncio.sleep(3)
        await conn.disconnect()

    except ValueError as e:
        # Log any value errors that occur during the process.
        logging.error(f"An error occurred: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Handle Ctrl+C to exit gracefully.
        print("\nProgram interrupted by user")
        sys.exit(0)