from flask import Flask, request, jsonify
from ibm_watson import AssistantV2
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from datetime import datetime
from cloudant.client import Cloudant
import os
import pandas as pd

app = Flask(__name__)

table_border_style = """
<style>
    /* CSS styles */
    h1    {
        font-family: 'Inter', sans-serif;
    }
    body  {
        font-family: 'Inter', sans-serif;    
    }
    table {
        border-collapse: collapse;
        border: 1px solid #ddd; /* Apply border to the whole table */
        font-family: 'Inter', sans-serif;
    }

    th, td {
        border: 1px solid #ddd; /* Apply border to cells */
        padding-top: 1;
        padding-bottom: 1;
        text-align: left;
        padding-left: 12px; /* Left padding for data cells */
        padding-right: 12px; /* Right padding for data cells */        
        font-family: 'Inter', sans-serif;
    }
    .grey-text {
        color: #aaaaaa;
    }   
    .red-text {
        color: red;
    }    
</style>
"""

menu_html = """
<h1>Watson Assistant FAQ Orchestrator</h1>
<button onclick="window.location.href = '/selection_log'">&nbsp;Faq Selections&nbsp;</button>&nbsp;&nbsp;
<button onclick="window.location.href = '/log'">&nbsp;Faq Logs&nbsp;</button>&nbsp;&nbsp;
<button onclick="window.location.href = '/config'">&nbsp;Faq Config&nbsp;</button>
<br><br>
"""

config_form_begin = """
<form action="/config" method="post">
    <label for="numbers">Select new maximum number of options (3 to 8):</label>
    <select id="numbers" name="selected_number">
"""

config_form_middle = """
    </select>
    <br><br>
    <label for="toggle">FAQ stripping (True/False):</label>
    <input type="checkbox" id="toggle" name="toggle_switch"    
"""

config_form_end = """>    
    <br><br>
    <input type="submit" value="Submit">
</form>
"""

MAXIMUM_LOG_ROWS = 100

class LoggerClass:
    def __init__(self, name):
        self.name = name
        columns = ["datetime", "level", "message", "indent"]
        self.log = pd.DataFrame(columns=columns)

    def add_row(self, level, message, indent=0):
        current_datetime = datetime.now()
        current_datetime_str = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
        self.log.loc[len(self.log.index)] = [
            current_datetime_str, level, message, indent]
        self.log = self.log.tail(MAXIMUM_LOG_ROWS)

    def info(self, message, indent=0):
        self.add_row("info", message, indent + 1)

    def debug(self, message, indent=0):
        self.add_row("debug", message, indent)

    def error(self, message, indent=0):
        self.add_row("error", message, indent)

    # Function to generate HTML table from DataFrame
    def generate_html_table(self):
        html_table = '<table><tr><th>Time</th><th>Type</th><th>Message</th></tr>'
        for index, row in self.log.iterrows():
            datetime_str = row["datetime"]
            message = row["message"]
            level = row["level"]
            indent = row["indent"]
            while indent > 0:
                message = "&nbsp;&nbsp;&nbsp;&nbsp;" + message
                indent -= 1
            if level == "info":
                html_table += f'<tr><td>{datetime_str}</td><td>{level}</td><td class="grey-text">{message}</td></tr>'
            elif level == "error":
                html_table += f'<tr><td>{datetime_str}</td><td>{level}</td><td class="red-text">{message}</td></tr>'
            else:
                html_table += f'<tr><td>{datetime_str}</td><td>{level}</td><td>{message}</td></tr>'
        html_table += '</table>'
        return html_table


class SelectionLoggerClass:
    def __init__(self, name):
        self.name = name
        columns = ["datetime", "query", "selected_faq",
                   "selected_conf", "top_faq", "top_conf", "ranking"]
        self.log = pd.DataFrame(columns=columns)

    def add_row(self, query, selected_faq, selected_conf, top_faq, top_conf, ranking):
        global logger
        global cloudant_client
        global cloudant_db

        current_datetime = datetime.now()
        current_datetime_str = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
        self.log.loc[len(self.log.index)] = [current_datetime_str,
                                             query, selected_faq, selected_conf, top_faq, top_conf, ranking]
        self.log = self.log.tail(MAXIMUM_LOG_ROWS)
        try:
            # test existing connection cloudant_client
            metadata = cloudant_client.metadata()
        except Exception as e:
            cloudant_client = Cloudant.iam(None, cloudant_apikey,
                                  url=cloudant_url, connect=True)
            logger.info("New cloudant connection created")
            # Get a reference to the database
            cloudant_db = cloudant_client[cloudant_dbname]

        # Create a new document
        new_selection = {
            "datetime_str": current_datetime_str,
            "datetime_iso": current_datetime.isoformat(),
            "datetime_float": current_datetime.timestamp(),
            "query": query,
            "selected_faq": selected_faq,
            "selected_conf": selected_conf,
            "top_faq": top_faq,
            "top_conf": top_conf,
            "ranking": ranking
        }

        # Insert the new document into the database
        new_doc = cloudant_db.create_document(new_selection)

        # Check that the document exists in the database
        if not new_doc.exists():
            logger.error(
                "Selection document has not been created in cloudant database")

    # Function to generate HTML table from DataFrame
    def generate_html_table(self):
        html_table = '<table><tr><th>Time</th><th>Query</th><th>Selected FAQ</th><th>Selected Conf</th><th>Top FAQ</th><th>Top Conf</th><th>Ranking</th></tr>'
        for index, row in self.log.iterrows():
            html_table += f'<tr><td>{row["datetime"]}</td><td>{row["query"]}</td><td>{row["selected_faq"]}</td><td>{row["selected_conf"]}</td><td>{row["top_faq"]}</td><td>{row["top_conf"]}</td><td>{row["ranking"]}</td></tr>'
        html_table += '</table>'
        return html_table


logger = LoggerClass("LOG")
selection_log = SelectionLoggerClass("SELECTION")


def wa_login():
    global authenticator
    global assistant
    global assistant_id
    global session_id
    global api_key
    global wa_url

    logger.debug("wa_login() new wa session start")
    authenticator = IAMAuthenticator(api_key)
    assistant = AssistantV2(
        version='2021-11-27',
        authenticator=authenticator)
    assistant.set_service_url(wa_url)
    session = assistant.create_session(assistant_id).get_result()
    session_id = session['session_id']
    logger.debug("wa_login() new wa session " + session_id)


def get_intent_text(intent_text):
    global logger
    global assistant_id
    global session_id
    global assistant

    result = assistant.message(
        assistant_id=assistant_id,
        session_id=session_id,
        input={
            'message_type': 'text',
            'text': '*',
            "intents": [
                {
                    "intent": intent_text,
                    "confidence": 1
                }
            ]
        }
    )
    if result.status_code == 200:
        response = result.get_result()
        if 'generic' in response['output']:
            # It is not random way to return text, for random need to be adjusted !!!
            return (response['output']['generic'][0]['text'])
        else:
            logger.error(
                "get_intent_text: Return json does not include generic and text")
            return ("Error: get_intent_text: Return json does not include generic and text")
    else:
        logger.error("get_intent_text: Wa did not get text for intent")
        return ("Error: get_intent_text: Wa did not get text for intent")


@app.route("/query", methods=['POST'])
def query_api():
    try:
        global logger
        global assistant_id
        global session_id
        global assistant
        global max_intents
        global faq_stripping
        global authenticator

        logger.debug("/query POST")
        request_data = request.get_json()
        if 'query' not in request_data:
            logger.error("Query: missing query parameter")
            return jsonify({"error": "Missing 'query' parameter"}), 400
        query = request_data['query']
        if not (type(query) is str):
            logger.error("Query: Wrong parameter type: " + type(query))
            return jsonify({"error": "Wrong 'query' parameter type"}), 400
        else:
            logger.info("Query: parameter: " + query)

        # Create WA session if it is not opened yet

        while (True):
            try:
                if not authenticator:
                    wa_login()
                # Get Intents for Query
                result = assistant.message(
                    assistant_id=assistant_id,
                    session_id=session_id,
                    input={
                        'message_type': 'text',
                        'text': query,
                        "options": {
                            "alternate_intents": True,
                        }
                    }
                )
                response = result.get_result()
                if result.status_code == 200 and 'intents' in response["output"]:
                    response_data = []
                    intents = response["output"]['intents']
                    count = 0
                    for intent in intents:
                        count += 1
                        if count <= max_intents:
                            intent_text = intent['intent']
                            if intent_text.startswith("fallback"):
                                count -= 1
                                continue
                            logger.info("Query: intent " + intent_text +
                                        f" C: {intent['confidence']:.4f}")
                            out_text = get_intent_text(intent_text)
                            intent_text_str = intent_text
                            # striping FAQ and '_'
                            if faq_stripping:
                                if intent_text_str.startswith('FAQ-'):
                                    intent_text_str = intent_text_str[len(
                                        'FAQ-'):]
                                intent_text_str = intent_text_str.replace(
                                    '_', ' ')

                            new_item = {
                                'intent': intent_text,
                                'intent_str': intent_text_str,
                                'text':  out_text,
                                'confidence': intent['confidence']
                            }
                            response_data.append(new_item)
                    logger.debug("/query return")
                    return jsonify(response_data)
                else:
                    logger.error("Query: Wa reponded with error")
                    return jsonify({"error": "Wa reponded with error"}), 400
            except Exception as e:
                if hasattr(e, "code"):
                    if e.code == 404:
                        authenticator = None
                        continue
                authenticator = None
                return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/selection", methods=['POST'])
def selection_api():
    global logger
    global selection_log

    try:
        logger.debug("/selection POST")
        request_data = request.get_json()
        if 'query' not in request_data:
            logger.error("Selection: missing query parameter")
            return jsonify({"error": "Missing 'query' parameter"}), 400
        else:
            query = request_data['query']
            logger.info("Selection query: " + query)
        if 'selected_name' not in request_data:
            logger.error("Selection: missing selected_name parameter")
            return jsonify({"error": "Missing 'selected_name' parameter"}), 400
        else:
            selected_name = request_data['selected_name']
            logger.info("Selection selected: " + selected_name)
        if 'selected_confidence' not in request_data:
            logger.error("Selection: missing selected_confidence parameter")
            return jsonify({"error": "Missing 'selected_confidence' parameter"}), 400
        else:
            selected_confidence = request_data['selected_confidence']
            logger.info(f"Selection confidence: C: {selected_confidence:.4f}")
        if 'top_name' in request_data:
            top_name = request_data['top_name']
        else:
            top_name = ""
        if 'top_confidence' in request_data:
            top_confidence = request_data['top_confidence']
        else:
            top_confidence = -1
        if 'ranking' in request_data:
            ranking = request_data['ranking']
        else:
            ranking = ""

        selection_log.add_row(query, selected_name,
                              selected_confidence, top_name, top_confidence, ranking)

        logger.debug("/selection return")
        return '', 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/", methods=['GET'])
@app.route("/log", methods=['GET'])
def log_web():
    global logger
    logger.debug("/log GET")
    html_in = "<HTML><HEAD>" + table_border_style + "</HEAD><BODY>" + menu_html
    html_out = "</BODY></HTML>"
    return (html_in + logger.generate_html_table() + html_out)


@app.route("/selection_log", methods=['GET'])
def selection_web():
    global selection_log
    logger.debug("/selection_log GET")
    html_in = "<HTML><HEAD>" + table_border_style + "</HEAD><BODY>" + menu_html
    html_out = "</BODY></HTML>"
    return (html_in + selection_log.generate_html_table() + html_out)


@app.route("/config", methods=['GET'])
def config_web():
    global max_intents
    global faq_stripping
    logger.debug("/config GET")
    html_in = "<HTML><HEAD>" + table_border_style + "</HEAD><BODY>" + menu_html
    html_out = "</BODY></HTML>"
    existing = f"<p>Existing maximum number of options: {max_intents}</p>"
    message = ""
    for i in range(2, 9):
        if i == max_intents:
            message += f'<option value="{i}" selected>{i}</option>\n'
        else:
            message += f'<option value="{i}">{i}</option>\n'

    toggle_message = " checked" if faq_stripping else " "
    return (html_in + existing + config_form_begin + message + config_form_middle + toggle_message + config_form_end + html_out)


@app.route('/config', methods=['POST'])
def config_submit():
    global max_intents
    global faq_stripping
    logger.debug("/config POST")
    max_intents = int(request.form['selected_number'])
    logger.info("MAX_INTENTS = " + str(max_intents))
    faq_stripping = ('toggle_switch' in request.form)
    # faq_stripping = (faq_stripping_str == "on" or faq_stripping_str == "1" or faq_stripping_str == "True")
    logger.info("FAQ_STRIPPING = " + str(faq_stripping))
    # logger.debug("FAQ_STRIPPING_STR = " + faq_stripping_str)

    html_in = "<HTML><HEAD>" + table_border_style + "</HEAD><BODY>" + menu_html
    html_out = "</BODY></HTML>"
    existing = f"<p>Existing maximum number of options: {max_intents}</p>"
    message = ""
    for i in range(2, 9):
        if i == max_intents:
            message += f'<option value="{i}" selected>{i}</option>\n'
        else:
            message += f'<option value="{i}">{i}</option>\n'
    toggle_message = " checked" if faq_stripping else " "
    return (html_in + existing + config_form_begin + message + config_form_middle + toggle_message + config_form_end + html_out)


@app.route("/kill", methods=['GET'])
def terminate_flask_server():
    logger.debug("/kill GET")
    os.kill(os.getpid(), 9)


# Log some messages
logger.info(
    "Title: Custom Extension to get response from Watson Assistant started")

# Get the PORT from environment
port = os.getenv('PORT', '8080')
# Get authenticate key
api_key = os.getenv('WA_API_KEY', 'None')
logger.debug("WA_API_KEY = " + api_key)
# Get authenticate url
wa_url = os.getenv('WA_URL', 'None')
logger.debug("WA_URL = " + wa_url)
# Get assistant_id
assistant_id = os.getenv('WA_ASSISTANT_ID', 'None')
logger.debug("WA_ASSISTANT_ID = " + assistant_id)
# Max Returned Intents
max_intents_str = os.getenv('MAX_INTENTS', '5')
max_intents = int(max_intents_str)
logger.debug("MAX_INTENTS = " + str(max_intents))
# FAQ stripping
faq_stripping_str = os.getenv('FAQ_STRIPPING', 'True')
faq_stripping = (faq_stripping_str == "True" or faq_stripping_str == "1")
logger.debug("FAQ_STRIPPING = " + str(faq_stripping))
# CLOUDANT
cloudant_url = os.getenv('CLOUDANT_URL', 'None')
logger.debug("CLOUDANT_URL = " + str(cloudant_url))
cloudant_apikey = os.getenv('CLOUDANT_APIKEY', 'None')
logger.debug("CLOUDANT_APIKEY = " + str(cloudant_apikey))
cloudant_dbname = os.getenv('CLOUDANT_DB', 'SELECTION')
logger.debug("CLOUDANT_DB = " + str(cloudant_dbname))

cloudant_client = None
cloudant_db = None

# Initiate WA connection (none)
authenticator = None
assistant = None

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(port))
