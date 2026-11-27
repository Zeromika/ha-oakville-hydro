# Oakville Hydro for Home Assistant

A Home Assistant custom integration that reads your electricity and water usage from the Oakville Hydro MyAccount portal and stores it as long-term statistics, so the Energy dashboard can show it.

This project is not affiliated with Oakville Hydro. It uses the same web API as the portal at https://myaccount.oakvillehydro.com, which is not documented and can change without notice.

## What it does

- Signs in with the email and password of your MyOakvilleHydro account.
- Reads hourly electricity usage (kWh) and the cost the portal reports for each hour.
- Reads water usage (m³) and its consumption charge for each billing period, when the account has a water service.
- Writes four statistics:
  - `oakville_hydro:electricity_consumption` in kWh
  - `oakville_hydro:electricity_cost` in your Home Assistant currency
  - `oakville_hydro:water_consumption` in m³
  - `oakville_hydro:water_cost` in your Home Assistant currency
- On first setup it imports all history the portal has.
- After that it checks every 6 hours. It reads the last 7 days of electricity again, so readings the utility corrects later replace the stored ones, and it adds a water billing period when the portal shows a new one.

### Water between two meter readings

The water meter is read about once every two months, and the portal has one volume per billing period. Totals per period are exact; when the water was used inside the period is not known. The time since the last reading stays empty until the next reading arrives.

The integration's **Configure** button (Settings → Devices & services → Oakville Hydro) has one setting for this:

- **Spread evenly over the days between the readings** (default). The volume and its cost are shared equally among the hours of the days after the previous reading up to and including the day of the reading. Values per day or month are averages.
- **All on the date of the meter reading.** The whole volume and cost appear on the day the meter was read, and the days before it show zero.

Changing the setting writes the stored water statistics again for every billing period the portal still lists. Older periods keep the form they were stored in.

It creates no entities. The data appears only as statistics.

## Limits

- The portal publishes each day's electricity readings during the following night. The data is always about one day behind and cannot show live power.
- The electricity cost is the electricity line of the bill only (the Time-of-Use, Tiered or Ultra-Low Overnight price). It does not include delivery, regulatory charges, HST or the Ontario Electricity Rebate.
- The water cost is the consumption charge only. It does not include the fixed service charge.
- A water billing period that the portal changes after it was imported is not updated.
- Accounts that ask for a one-time code at sign-in are not supported.
- One electricity meter and one water meter per Home Assistant. If the account has several, the first of each is used.

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
5. For water, add a water source on the same page and pick **Oakville Hydro water**, with **Oakville Hydro water cost (consumption charge)** as its cost.

When the portal rejects the stored password, for example after the yearly password expiry, Home Assistant asks you to sign in again.

## How the statistics are kept consistent

Each statistic row carries a running sum. On every update the integration reads the stored sum for the hour before the first hour it is about to write and continues from it. Hours that are already stored but missing from a later portal answer keep their stored value. Running the same update twice writes the same rows.

## Licence

MIT. See `LICENSE`.

## Development

The parsing and running-sum logic is in `custom_components/oakville_hydro/model.py` and has no Home Assistant imports. Its tests run with:

```
python -m pytest tests
```
