# Tart-Pi Engineering Dashboard

A Raspberry Pi–based system monitoring platform designed to explore embedded systems, backend services, and infrastructure engineering.

## Dashboard

The monitoring dashboard displays live telemetry collected from the Raspberry Pi in real time.

![Tart-Pi Dashboard](tart-pi-dashboard.png)

## Dashboard

### System Overview
![Dashboard Overview](tart-pi-dashboard.png)

### CPU Usage Monitoring
![CPU Graph](tart-pi-dashboard-cpu.png)

### Memory Usage Monitoring
![Memory Graph](tart-pi-dashboard-memory.png)

### CPU Temperature Monitoring
![Temperature Graph](tart-pi-dashboard-temperature.png)

## Overview

This project runs a persistent monitoring service on a Raspberry Pi that collects and logs system telemetry including:

- CPU utilization
- CPU temperature
- Memory usage
- Disk usage
- System load averages
- Uptime
- Process count

Metrics are sampled every 5 seconds and stored in a SQLite time-series database.

## Architecture

Raspberry Pi (Debian Linux)

FastAPI Backend  
Background Telemetry Worker  
SQLite Time-Series Database  
REST API Endpoints  
Web Dashboard  

## API Endpoints

`/metrics`
Returns current system metrics.

`/metrics/history`
Returns historical metrics with optional downsampling.

`/dashboard`
Web dashboard displaying system telemetry.

## Deployment

The service runs as a persistent Linux service using **systemd** and automatically starts on boot.

## Technologies

Python  
FastAPI  
SQLite  
systemd  
Linux (Debian on ARM)
