# Oakville Hydro for Home Assistant

A Home Assistant custom integration that reads your hourly electricity usage from the Oakville Hydro MyAccount portal and stores it as long-term statistics, so the Energy dashboard can show it.

This project is not affiliated with Oakville Hydro. It uses the same web API as the portal at https://myaccount.oakvillehydro.com, which is not documented and can change without notice.

## What it does

- Signs in with the email and password of your MyOakvilleHydro account.
- Reads hourly usage (kWh) and the cost the portal reports for each hour.
- Writes two statistics:
  - `oakville_hydro:electricity_consumption` in kWh
  - `oakville_hydro:electricity_cost` in your Home Assistant currency
- On first setup it imports all hourly history the portal has, starting from the date your service began.
- After that it checks every 6 hours and reads the last 7 days again, so readings the utility corrects later replace the stored ones.

It creates no entities. The data appears only as statistics.

## Limits

- The portal publishes each day's readings during the following night. The data is always about one day behind and cannot show live power.
- The cost is the electricity line of the bill only (the Time-of-Use, Tiered or Ultra-Low Overnight price). It does not include delivery, regulatory charges, HST or the Ontario Electricity Rebate.
- Accounts that ask for a one-time code at sign-in are not supported.
- One electricity meter per Home Assistant. If the account has several, the first one is used.
- Water usage is not read.

## Requirements

Home Assistant 2026.11 or newer.

## Install

### HACS

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/Zeromika/ha-oakville-hydro` with the type **Integration**.
3. Install **Oakville Hydro** and restart Home Assistant.

### By hand

1. Copy the folder `custom_components/oakville_hydro` into the `custom_components` folder of your Home Assistant configuration directory.
2. Restart Home Assistant.

## Set up

1. Go to **Settings → Devices & services → Add integration** and choose **Oakville Hydro**.
2. Enter the email and password of your MyOakvilleHydro account. Home Assistant stores them in the integration's config entry, the same way it stores other integrations' credentials.
3. The first import runs in the background and takes one to three minutes for a year of history.
4. Go to **Settings → Dashboards → Energy**, add a grid consumption source, and pick **Oakville Hydro electricity**. For the cost, choose "Use an entity tracking the total costs" and pick **Oakville Hydro electricity cost (commodity)**.

When the portal rejects the stored password, for example after the yearly password expiry, Home Assistant asks you to sign in again.

## How the statistics are kept consistent

Each statistic row carries a running sum. On every update the integration reads the stored sum for the hour before the first hour it is about to write and continues from it. Hours that are already stored but missing from a later portal answer keep their stored value. Running the same update twice writes the same rows.

## Development

The parsing and running-sum logic is in `custom_components/oakville_hydro/model.py` and has no Home Assistant imports. Its tests run with:

```
python -m pytest tests
```
