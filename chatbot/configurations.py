import streamlit as st
import time
import ollama
from langchain.memory.chat_message_histories import StreamlitChatMessageHistory

st.set_page_config(page_title="Ollama Chatbot", page_icon="💬")

with st.sidebar:
    st.title('💬 Ollama Chatbot')
    
    st.divider()
    # Select the model
    selected_model = st.selectbox('Choose a model', ['Mistral', 'Solar', 'Code Llama'], key='selected_model')
    
    if selected_model == "Mistral":
        llm_model = "mistral"
        st.caption("""
                   The Mistral 7B model released by Mistral AI. 
                   Mistral 7B model is an Apache licensed 7.3B parameter model. 
                   It is available in both instruct (instruction following) and text completion.
                   """) 
    elif selected_model == "Solar":
        llm_model = "Solar"
        st.caption("""
                    SOLAR 10.7B is released by Upstage. 
                    Solar a 10B an open LLM outperforming other LLMs up to 30B parameters, 
                    including Mistral 7B. 🤯 Solar achieves an MMLU score of 65.48, 
                    which is only 4 points lower than Meta Llama 2 while being 7x smaller.
                   """) 
    else:
        llm_model = "codellama"
        st.caption("""
                   Code Llama is a model for generating and discussing code, built on top of Llama 2. 
                   It’s designed to make workflows faster and efficient for developers and make it easier for people to learn how to code. 
                   It can generate both code and natural language about code. 
                   Code Llama supports many of the most popular programming languages used today, including Python, C++, Java, PHP, Typescript (Javascript), C#, Bash and more.
                   """) 
    st.divider()

system_prompt = st.text_area(
            label="System Prompt",
            value="You are a helpful assistant who answers questions in short sentences."
            )