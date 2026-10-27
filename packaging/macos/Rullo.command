#!/bin/sh
# Double-click to start Rullo; this Terminal window stays open while you ride.
cd "$(dirname "$0")"
exec ./rullo/rullo app
