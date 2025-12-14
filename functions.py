from utils import RequestClient, CampaignListResponse, Campaign, Timeframe, Filter
from typing import Dict, Any, List
import json


def parse_campaign_data(data: Dict[str, Any]) -> Campaign:
    """
    Helper function to convert a raw dictionary into a Campaign dataclass,
    handling nested objects (Timeframe, Filter) and kw_only arguments.
    """
    
    # 1. Handle nested 'timeframe' (Mandatory in your JSON)
    tf_data = data.pop("timeframe")
    timeframe_obj = Timeframe(**tf_data)

    # 2. Handle nested 'filter' (Optional in your JSON)
    filter_obj = None
    if "filter" in data and data["filter"] is not None:
        f_data = data.pop("filter")
        filter_obj = Filter(**f_data)
    
    # 3. Create Campaign object
    # We unpack the rest of the primitive data using **data
    return Campaign(
        timeframe=timeframe_obj,
        filter=filter_obj,
        **data
    )

def return_campaign_object(client, log):
    rc, response = client.get(endpoint="/jobs")

    if rc != 0 or not response:
        log.error("Failed to fetch jobs.")
        return

    try:
        json_payload = response.json()
    except json.JSONDecodeError:
        log.error("Response was not valid JSON.")
        return
    
    # The JSON wrapper key is "campaigns"
    raw_campaign_list = json_payload.get("campaigns", [])
    parsed_campaigns: List[Campaign] = []

    for item in raw_campaign_list:
        try:
            # Convert dict to Campaign object
            camp_obj = parse_campaign_data(item)
            parsed_campaigns.append(camp_obj)
        except TypeError as e:
            log.error(f"Error parsing campaign ID {item.get('campaign_id')}: {e}")

    # Wrap in the Response Object (optional, but good for type safety)
    return CampaignListResponse(campaigns=parsed_campaigns)