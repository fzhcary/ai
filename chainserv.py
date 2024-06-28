#!/usr/bin/env python
import getpass
import os
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain.chains import create_sql_query_chain
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from operator import itemgetter
from langchain_core.runnables import RunnablePassthrough
from langserve import add_routes

# setup
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = getpass.getpass()

# Initialize FastAPI app
app = FastAPI(
    title="LangChain Server",
    version="1.0",
    description="A simple API server using LangChain's Runnable interfaces",
)

# HTML form to collect database location and query
html_form = """
<!DOCTYPE html>
<html>
<head>
    <title>SQL Query Interface</title>
</head>
<body>
    <h1>Enter Database Details and Query</h1>
    <form action="/query_sql" method="post">
        <label for="db_type">Database Type:</label><br>
        <select id="db_type" name="db_type">
            <option value="sqlite">SQLite</option>
            <option value="sqlserver">SQL Server</option>
            <option value="databricks">Databricks Delta Table</option>
            <option value="salesforce">Salesforce</option>
        </select><br><br>
        <label for="db_location">Database Location/Connection String:</label><br>
        <input type="text" id="db_location" name="db_location"><br><br>
        <div id="sqlserver_credentials" style="display:none;">
            <label for="db_username">Username:</label><br>
            <input type="text" id="db_username" name="db_username"><br><br>
            <label for="db_password">Password:</label><br>
            <input type="password" id="db_password" name="db_password"><br><br>
        </div>
        <div id="databricks_credentials" style="display:none;">
            <label for="databricks_host">Databricks Host:</label><br>
            <input type="text" id="databricks_host" name="databricks_host"><br><br>
            <label for="databricks_token">Databricks Token:</label><br>
            <input type="password" id="databricks_token" name="databricks_token"><br><br>
        </div>
        <div id="salesforce_credentials" style="display:none;">
            <label for="sf_username">Salesforce Username:</label><br>
            <input type="text" id="sf_username" name="sf_username"><br><br>
            <label for="sf_password">Salesforce Password:</label><br>
            <input type="password" id="sf_password" name="sf_password"><br><br>
            <label for="sf_token">Salesforce Security Token:</label><br>
            <input type="password" id="sf_token" name="sf_token"><br><br>
        </div>
        <label for="query">Query:</label><br>
        <input type="text" id="query" name="query"><br><br>
        <input type="submit" value="Submit">
    </form>
    <script>
        document.getElementById('db_type').addEventListener('change', function() {
            var sqlserverDisplay = this.value == 'sqlserver' ? 'block' : 'none';
            var databricksDisplay = this.value == 'databricks' ? 'block' : 'none';
            var salesforceDisplay = this.value == 'salesforce' ? 'block' : 'none';
            document.getElementById('sqlserver_credentials').style.display = sqlserverDisplay;
            document.getElementById('databricks_credentials').style.display = databricksDisplay;
            document.getElementById('salesforce_credentials').style.display = salesforceDisplay;
        });
    </script>
</body>
</html>
"""

# Define the main route to serve the HTML form
@app.get("/", response_class=HTMLResponse)
async def get_form():
    return html_form

# Create LLM model
llm = ChatOpenAI(model="gpt-3.5-turbo-0125")

# Define the answer prompt
answer_prompt = PromptTemplate.from_template(
    """Given the following user question, corresponding SQL query, and SQL result, answer the user question.

Question: {question}
SQL Query: {query}
SQL Result: {result}
Answer: """
)

# Define the endpoint to accept database location and query
@app.post("/query_sql", response_class=HTMLResponse)
async def query_sql(
    db_type: str = Form(...),
    db_location: str = Form(...),
    query: str = Form(...),
    db_username: str = Form(None),
    db_password: str = Form(None),
    databricks_host: str = Form(None),
    databricks_token: str = Form(None),
    sf_username: str = Form(None),
    sf_password: str = Form(None),
    sf_token: str = Form(None)
):
    if not db_location or not query:
        return {"error": "Database location and query are required"}

    # Create a new database connection based on the db_type
    if db_type == "sqlite":
        db = SQLDatabase.from_uri(f"sqlite:///{db_location}")
    elif db_type == "sqlserver":
        if not db_username or not db_password:
            return {"error": "Username and password are required for SQL Server"}
        db = SQLDatabase.from_uri(f"mssql+pyodbc://{db_username}:{db_password}@{db_location}?driver=ODBC+Driver+17+for+SQL+Server")
    elif db_type == "databricks":
        if not databricks_host or not databricks_token:
            return {"error": "Host and token are required for Databricks"}
        db = SQLDatabase.from_uri(f"databricks+connector://token:{databricks_token}@{databricks_host}")
    elif db_type == "salesforce":
        if not sf_username or not sf_password or not sf_token:
            return {"error": "Username, password, and security token are required for Salesforce"}
        from simple_salesforce import Salesforce
        sf = Salesforce(instance_url=db_location, username=sf_username, password=sf_password, security_token=sf_token)
        db = SQLDatabase(connection=sf)
    else:
        return {"error": "Unsupported database type"}

    # Set up the query chain with the new database
    execute_query = QuerySQLDataBaseTool(db=db)
    write_query = create_sql_query_chain(llm, db, k=30)

    # Debugging: Generate the query from the input question
    generated_query = write_query.invoke({"question": query})
    print(f"Generated SQL query: {generated_query}")

    # Set up the final chain
    final_chain = (
        RunnablePassthrough.assign(query=write_query).assign(
            result=itemgetter("query") | execute_query
        )
        | answer_prompt
        | llm
        | StrOutputParser()
    )

    # Invoke the chain with the user query
    result = final_chain.invoke({"question": query})

    # Return the result as HTML
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>SQL Query Result</title>
    </head>
    <body>
        <h1>Query Result</h1>
        <p>Question: {query}</p>
        <p>Generated SQL query: {generated_query}</p>
        <p>Result: {result}</p>
        <a href="/">Go Back</a>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8001)
