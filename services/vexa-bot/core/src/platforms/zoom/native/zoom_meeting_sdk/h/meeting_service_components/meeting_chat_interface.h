/**
 * @file meeting_chat_interface.h
 * @brief Meeting Service Chat Interface. 
 * @note Valid for both ZOOM style and user custom interface mode.
 */
#ifndef _MEETING_CHAT_INTERFACE_H_
#define _MEETING_CHAT_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE
/**
 * @brief Enumerations of the type for chat message.
 */
typedef enum
{
	/** For initialize */
	SDKChatMessageType_To_None, 
	/** Chat message is sent to all. */
	SDKChatMessageType_To_All,	
	/** Chat message is sent to all panelists. */
	SDKChatMessageType_To_All_Panelist,
	/** Chat message is sent to individual attendee and cc panelists. */
	SDKChatMessageType_To_Individual_Panelist,
	/** Chat message is sent to individual user. */
	SDKChatMessageType_To_Individual,
	/** Chat message is sent to waiting room user. */
	SDKChatMessageType_To_WaitingRoomUsers,
}SDKChatMessageType;

/**
 * @brief Bold attributes.
 */
struct BoldAttrs
{
	/** If the value is true, the text style is Bold */
	bool bBold = false; 
};

/** 
 * @brief Italic attributes.
 */
struct ItalicAttrs
{
	/** If the value is true, the text style is Italic. */
	bool bItalic = false; 
};

/** 
 * @brief Strikethrough attributes.
 */
struct StrikethroughAttrs
{
	/** If the value is true, the text style is Strikethrough. */
	bool bStrikethrough = false; 
};

// \brief BulletedList attributes.
struct BulletedListAttrs
{
	/** If the value is true, the text style is BulletedList. */
	bool bBulletedList = false; 
};

/** 
 * @brief NumberedList attributes.
 */
struct NumberedListAttrs
{
	/** If the value is true, the text style is Numbered. */
	bool bNumberedList = false; 
};

/** 
 * @brief Underline attributes.
 */
struct UnderlineAttrs
{
	/** If the value is true, the text style is Underline. */
	bool bUnderline = false; 
};

/** 
 * @brief Quote attributes.
 */
struct QuoteAttrs
{
	/** If the value is true, the text style is Quote. */
	bool bQuote = false; 
};

/** 
 * @brief InsertLink attributes.
 */
struct InsertLinkAttrs {
	/** If insertLinkUrl is not empty, the text style has insert link url. */
    const zchar_t* insertLinkUrl = nullptr; 
};

/** 
 * font size for TextStyle_FontSize
 */
#define RichTextStyle_FontSize_Small		_T("FontSize_Small")
#define RichTextStyle_FontSize_Medium	    _T("FontSize_Medium")
#define RichTextStyle_FontSize_Large		_T("FontSize_Large")

/**
 * Currently supported font size value.
 */
#define FontSize_Small 8
#define FontSize_Medium 10
#define FontSize_Large 12

/** 
 * @brief FontSize attributes.
 */
struct FontSizeAttrs {
	/**  Font size value. */
    int fontSize = 0; 
};

/**
 * Currently supported font color combinations.
 * FontColor_Red, 235,24,7
 * FontColor_Orange, 255,138,0
 * FontColor_Yellow, 248,194,0
 * FontColor_Green, 19,138,0
 * FontColor_Blue, 0,111,250
 * FontColor_Violet, 152,70,255
 * FontColor_Rosered, 226,0,148
 * FontColor_Black 34,34,48
 */ 
 /** 
  * @brief FontColor attributes. The standard RGB color model has a value range of 0-255.
  */
struct FontColorAttrs {
	/** Font color R value. */
	unsigned int red = 0; 
	/** Font color G value. */
	unsigned int green = 0; 
	/** Font color B value. */
	unsigned int blue = 0; 
};

/**
 * Currently supported background color combinations.
 * BackgroundColor_Normal, 255,255,255
 * BackgroundColor_Red, 255,67,67
 * BackgroundColor_Orange, 255,138,0
 * BackgroundColor_Yellow, 255,214,0
 * BackgroundColor_Green, 73,214,30
 * BackgroundColor_Blue, 47,139,255
 * BackgroundColor_Violet, 171,104,255
 * BackgroundColor_Rosered, 255,54,199
 * BackgroundColor_White 255,255,255
 */
/** 
 * @brief FontColor attributes. The standard RGB color model has a value range of 0-255.
 */
struct BackgroundColorAttrs {
	/** Background color R value. */
	unsigned int red = 0; 
	/** Background color G value. */
	unsigned int green = 0; 
	/** Background color B value. */
	unsigned int blue = 0; 
};

/**
 * The attribute strParagraph can be set with value below.
 * If need customized paragraph text style, define strParagraph as the name of the customized text style and it will be shown in IChatMsgInfo.
 */
#define RichTextStyle_Paragraph_H1		_T("Paragraph_H1")
#define RichTextStyle_Paragraph_H2		_T("Paragraph_H2")
#define RichTextStyle_Paragraph_H3		_T("Paragraph_H3")
 
/**
 * @brief FontColor attributes.
 */
struct ParagraphAttrs {
	/** If strParagraph is not empty, the text style has Paragraph style. */
    const zchar_t* strParagraph = nullptr; 
};

/** 
 * @brief Indent attributes.
 */
struct IndentAttrs {
	/** The number of times the indentation style is applied. */
    unsigned int indent = 0; 
};

/**
 * @brief information of rich text with style attributes in a chat message content.
 * Here are more detailed structural descriptions.
 */
struct SegmentDetails {
	/** Segment content value. */
	const zchar_t* strContent = nullptr; 
	/** Segment BoldAttrs value. */
	BoldAttrs boldAttrs = {}; 
	/** Segment ItalicAttrs value. */
	ItalicAttrs italicAttrs = {}; 
	/** StrikethroughAttrs content value. */
	StrikethroughAttrs strikethroughAttrs = {}; 
	/** Segment BulletedListAttrs value. */
	BulletedListAttrs bulletedListAttrs = {}; 
	/** Segment NumberedListAttrs value. */
	NumberedListAttrs numberedListAttrs = {}; 
	/** Segment UnderlineAttrs value. */
	UnderlineAttrs underlineAttrs = {}; 
	/** Segment QuoteAttrs value. */
	QuoteAttrs quoteAttrs = {}; 
	/** Segment InsertLinkAttrs value. */
	InsertLinkAttrs insertLinkAttrs = {}; 
	/** Segment FontSizeAttrs value. */
	FontSizeAttrs fontSizeAttrs = {}; 
	/** Segment FontColorAttrs value. */
	FontColorAttrs fontColorAttrs = {}; 
	/** Segment BackgroundColorAttrs value. */
	BackgroundColorAttrs backgroundColorAttrs = {}; 
	/** Segment ParagraphAttrs value. */
	ParagraphAttrs paragraphAttrs = {}; 
	/** Segment IndentAttrs value. */
	IndentAttrs indentAttrs = {}; 
};

/**
 * @brief Enumerations of the content rich text style type for chat message.
 */
typedef enum
{
	/** Chat message rich text style normal. */
	TextStyle_None, 
	/** Chat message rich text style bold. */
	TextStyle_Bold, 
	/** Chat message rich text style italic. */
	TextStyle_Italic,
	/** Chat message rich text style strike through. */
	TextStyle_Strikethrough, 
	/** Chat message rich text style bulleted list. */
	TextStyle_BulletedList, 
	/** Chat message rich text style numbered list. */
	TextStyle_NumberedList, 
	/** Chat message rich text style underline. */
	TextStyle_Underline, 
	/** Chat message rich text style font size. */
	TextStyle_FontSize, 
	/** Chat message rich text style font color. */
	TextStyle_FontColor, 
	/** Chat message rich text style background color. */
	TextStyle_BackgroundColor, 
	/** Chat message rich text style indent. */
	TextStyle_Indent,
	/** Chat message rich text style paragraph. */
	TextStyle_Paragraph, 
	/** Chat message rich text style quote. */
	TextStyle_Quote, 
	/** Chat message rich text style insert link. */
	TextStyle_InsertLink 
}RichTextStyle;

/** 
 * @class IRichTextStyleOffset
 * @brief Chat message rich text style offset.
 */
class IRichTextStyleOffset
{
public:
	/**
     * @brief Gets the start position of a certain rich-text style.
	 * @return If the function succeeds, it returns the start position of the specified rich-text style. Otherwise, this function returns -1.
	 */
	virtual unsigned int GetPositionStart() = 0;

	/**
	 * @brief Gets the end position of a certain rich-text style.
	 * @return If the function succeeds, it returns the end position of the specified rich-text style. Otherwise, this function returns -1.
	 */
	virtual unsigned int GetPositionEnd() = 0;

	/**
     * @brief Gets the supplementary information of a certain rich-text style.
	 * @return If the function succeeds, it returns the supplementary information of the specified rich-text style. Otherwise, this function fails and returns nullptr.
	 * If the style is TextStyle_FontSize, possible return values are FontSize_Small, FontSize_Medium, or FontSize_Large.
	 * If the style is TextStyle_Paragraph, possible return values are Paragraph_H1, Paragraph_H2, or Paragraph_H3.
	 * If the style is TextStyle_FontColor or TextStyle_BackgroundColor, possible return values are hex string representing standard RGB data.
	 * If the function fails, the return value is a string of length zero(0).
	 */
	virtual const zchar_t* GetReserve() = 0;
};

/** 
 * @class IRichTextStyleItem
 * @brief Chat message rich text item of a certain style.
 */
class IRichTextStyleItem
{
public:
	/**
	 * @brief Gets the rich text type of a portion of the current message.
	 * @return If the function succeeds, it returns the rich text type of the specified portion of the current message. Otherwise, this function returns an error.
	 */
	virtual RichTextStyle GetTextStyle() = 0;

	/**
	 * @brief Gets the rich text position info list of a certain style for the current message.
	 * @return If the function succeeds, it returns the rich text position info list of a certain style for the current message. Otherwise, this function fails and returns nullptr.
	 */
	virtual IList<IRichTextStyleOffset*>* GetTextStyleOffsetList() = 0;
};


/**
 * @class IChatMsgInfo
 * @brief Chat message interface.
 */
class IChatMsgInfo
{
public:
	/** 
	 * @brief Gets the message ID of the current message.
	 * @return If the function succeeds, the return value is the message ID of the current message. Otherwise, this function returns an error, the return value is the string of length zero(0)
	 */
	virtual const zchar_t* GetMessageID() = 0;

	/** 
	 * @brief Gets the sender ID of the current message.
	 * @return If the function succeeds, the return value is the sender ID of the current message. Otherwise, this function returns an error, the return value is -1.
	 */
	virtual unsigned int GetSenderUserId() = 0;

	/** 
	 * @brief Gets the sender screen name of the current message.
	 * @return If the function succeeds, the return value is sender screen name of the current message. Otherwise, this function returns an error, the return value is nullptr.
	 * @note If the message is sent to all or to all panelists, the return value will be nullptr.
	 */
	virtual const zchar_t* GetSenderDisplayName() = 0;

	/** 
	 * @brief Gets the receiver ID of the current message.
	 * @return If the function succeeds, it returns the receiver ID of the current message.
	 * ZERO(0) indicates that the message is sent to all.
	 * ONE(1) indicates that the messages are sent to all the panelists.
	 * Otherwise, the return value is negative ONE(-1).
	 */
	virtual unsigned int GetReceiverUserId() = 0;

	/**
	 * @brief Gets the receiver screen name of the current message.
	 * @return If the function succeeds, the return value is the receiver screen name of the current message. Otherwise, the return value is the string of length zero(0).
	 */
	virtual const zchar_t* GetReceiverDisplayName() = 0;

	/** 
	 * @brief Gets the content of the current message.
	 * @return If the function succeeds, the return value is the pointer to the content of the current message. Otherwise, the return value is nullptr.
	 */
	virtual const zchar_t* GetContent() = 0;

	/**
	 * @brief Gets the timestamps of the current message.
	 * @return If the function succeeds, the return value is the timestamps of the current message. 
	 */
	virtual time_t GetTimeStamp() = 0;

	/**
	 * @brief Determines if the current message is sent to all. 
	 * @return true if the current message is sent to all. Otherwise, false.
	 */
	virtual bool IsChatToAll() = 0;

	/**
	 * @brief Determines if the current message is sent to all the panelists.
	 * @return true if the current message is sent to all the panelists. Otherwise, false.
	 */
	virtual bool IsChatToAllPanelist() = 0;

	/**
	 * @brief Determines if the current message is sent to waiting room.
	 * @return true if the current message is sent to waiting room. Otherwise, false.
	 */
	virtual bool IsChatToWaitingroom() = 0;

	/** 
	 * @brief Gets the chat message type of the current message.
	 * @return If the function succeeds, it returns the chat message type of the current message.
	 */
	virtual SDKChatMessageType GetChatMessageType() = 0;

	/** 
	 * @brief Determines if the current message is a reply to another message. 
	 * @return true if the current message is a reply to another message. Otherwise, false.
	 */
	virtual bool IsComment() = 0;

	/** 
	 * @brief Determines if the current message is part of a message thread, and can be directly replied to. 
	 * @return true if the current message is a part of a message thread. Otherwise, false.
	 */
	virtual bool IsThread() = 0;

	/**
	 * @brief Gets the current message's chat message font style list.
	 * @deprecated This class is marked as deprecated
	 */
	virtual IList<IRichTextStyleItem*>* GetTextStyleItemList() = 0;

	/**
	 * @brief Gets the chat message segment content and style detail of the current message.
	 * When receiving rich-text messages, a list of isolated paragraphs is included,
	 * each formatted according to its style. Concatenating these paragraphs
	 * together forms the complete message text.
	 */
	virtual IList<SegmentDetails>* GetSegmentDetails() = 0;

	/** 
	 * @brief Gets the current message's thread ID.
	 * @return If the function succeeds, the return value is the current message's thread ID. Otherwise, the return value is the string of length zero(0)
	 */
	virtual const zchar_t* GetThreadID() = 0;

	virtual ~IChatMsgInfo() {};
};

/** 
 * @brief The authority to chat in the normal meeting.  
 * Here are more detailed structural descriptions..
 */
typedef struct tagNormalMeetingChatStatus
{
	/** true indicates that the user owns the authority to send message to chat. */
	bool can_chat;
	/** true indicates that the user owns the authority to send message to all. */
	bool can_chat_to_all;
	/** true indicates that the user owns the authority to send message to an individual attendee in the meeting. */
	bool can_chat_to_individual;
	/** true indicates that the user owns the authority to send message only to the host. */
	bool is_only_can_chat_to_host; 
}NormalMeetingChatStatus;

/** 
 * @brief The authority to chat for the normal attendee in the webinar.
 * Here are more detailed structural descriptions..
 */
typedef struct tagWebinarAttendeeChatStatus
{
	/** true indicates that the attendee can send message to chat. */
	bool can_chat; 
	/** true indicates that the user owns the authority to send message to all the panelists and attendees. */
	bool can_chat_to_all_panellist_and_attendee;
	/** true indicates that the user owns the authority to send message to all the panelists. */
	bool can_chat_to_all_panellist;
}WebinarAttendeeChatStatus;

/** 
 * @brief The authority to chat for the host, co-host and panelist to chat in webinar.
 * Here are more detailed structural descriptions..
 */
typedef struct tagWebinarOtherUserRoleChatStatus
{
	/** true indicates that the user owns the authority to send message to all the panelists. */
	bool can_chat_to_all_panellist;
	/** true indicates that the user owns the authority to send message to all. */
	bool can_chat_to_all_panellist_and_attendee;
	/** true indicates that the user owns the authority to send message to individual attendee. */
	bool can_chat_to_individual;
}WebinarOtherUserRoleChatStatus;

/** 
 * @brief The authority to chat in the specified meeting.
 * Here are more detailed structural descriptions..
 */
typedef struct tagChatStatus
{
	/**
	 * The ut value depends on the value of the other members in the structure. 
	 * When the value of is_webinar_meeting is false, the ut value is the NormalMeetingChatStausnormal_meeting_status. 
	 * When the values of the is_webinar_meeting and the is_webinar_attendee is true, the ut value is WebinarAttendeeChatStatus webinar_attendee_status. 
	 * The value of is_webinar_meeting is true while the is_webinar_attendee is false, the ut value is WebinarOtherUserRoleChatStatus webinar_other_status.
	 */
	union
	{
		NormalMeetingChatStatus normal_meeting_status;
		WebinarAttendeeChatStatus webinar_attendee_status;
		WebinarOtherUserRoleChatStatus webinar_other_status;
	}ut;
	/** true indicates that it is disabled to chat in the specified meeting.  */
	bool is_chat_off;
	/** true indicates that the owner of the current message is the attendee of the webinar.  */
	bool is_webinar_attendee;
	/** true indicates that the current meeting is webinar. */
	bool is_webinar_meeting;

	tagChatStatus()
	{
		Reset();
	}

	void Reset()
	{
		memset(this, 0, sizeof(tagChatStatus));  //checked safe
	}
}ChatStatus;

/**
 * @brief Enumerations of the chat priviledge.
 */
typedef enum {
	/** allow attendee to chat with everyone [meeting & webinar] */
	SDK_CHAT_PRIVILEGE_ALL = 1, 
	/** allow attendee to chat with all panelists only, but cannot to "all panelists and attendees" [webinar] */
	SDK_CHAT_PRIVILEGE_ALL_PANELIST = 2,
	/** allow attendee to chat with host only [meeting] */
	SDK_CHAT_PRIVILEGE_HOST = 3,	
	/** allow attendee to chat with no one [meeting & webinar] */
	SDK_CHAT_PRIVILEGE_DISABLE_ATTENDEE_CHAT = 4,  
	/** allow attendee to chat with host and public [meeting] */
	SDK_CHAT_PRIVILEGE_HOST_PUBLIC = 5,              
	SDK_CHAT_PRIVILEGE_END
} SDKChatPrivilege;

/**
 * @brief Enumerations of the chat message delete type.
 */
typedef enum
{
	/** none */
	SDK_CHAT_DELETE_BY_NONE,	
	/** delete by self */
	SDK_CHAT_DELETE_BY_SELF,	
	/** delete by host */
	SDK_CHAT_DELETE_BY_HOST,	
	/** delete by dlp when the message goes against the host organization's compliance policies. */
	SDK_CHAT_DELETE_BY_DLP,		
}SDKChatMessageDeleteType;


typedef enum
{
	/**  The file transfer has no state. */
	SDKFileTransferState_None = 0,
	/**  The file transfer is ready to start. */
	SDKFileTransferState_ReadyToTransfer,  
	/**  The file transfer is in progress. */
	SDKFileTransferState_Transfering,  
	/**  The file transfer failed. */
	SDKFileTransferState_TransferFailed, 
	/**  The file transfer completed successfully. */
	SDKFileTransferState_TransferDone,     
}SDKFileTransferStatus;

/** 
 * @brief The basic information of transfer file
 */
typedef struct tagSDKFileTransferInfo
{
	/** The message identify of transfer file. */
	const zchar_t* messageID;
	/**  he status of the file transfer. */
	SDKFileTransferStatus trans_status;
	/** The time stamp of the file. */
	time_t time_stamp;
	/** Is the file send to all user in meeting. */
	bool is_send_to_all;
	/** The bytes of transfer file size. */
	unsigned int file_size;
	/** the file name of transfer file. */
	const zchar_t* file_name;
	/** The percentage of the file transfer completed. */
	unsigned int complete_percentage;
	/** The size of the file transferred so far in bytes. */
	unsigned int complete_size;
	/** The speed of the file transfer in bits per second. */
	unsigned int bit_per_second;
	tagSDKFileTransferInfo()
	{
		Reset();
	}

	void Reset()
	{
		memset(this, 0, sizeof(tagSDKFileTransferInfo));  //checked safe
	}
}SDKFileTransferInfo;

class ISDKFileSender
{
public:
	virtual SDKFileTransferInfo* GetTransferInfo() = 0;
	/** 
	 * @brief Gets file receiver's user id.
	 * @return The receiver user id. -1 specify the internel error of get user id. 0 specify the file send to all.
	 */
	virtual unsigned int GetReceiver() = 0;

	/**
	 * @brief Cancel the file send.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CancelSend() = 0;

	virtual ~ISDKFileSender() {};
};

class ISDKFileReceiver
{
public:
	virtual SDKFileTransferInfo* GetTransferInfo() = 0;

	/** 
	 * @brief Gets file sender's user id.
	 * @return The receiver user id. -1 specify the internel error of get user id. 0 specify the file send to all.
	 */
	virtual unsigned int GetSender() = 0;

	/**
	 * @brief Cancel the file receive.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CancelReceive() = 0;

	/**
	 * @brief Starts receive the file.
	 * @param path The path to receive the file.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StartReceive(const zchar_t* path) = 0;

	virtual ~ISDKFileReceiver() {};
};


/**
 * @class IMeetingChatCtrlEvent
 * @brief Meeting chat callback event.
 */
class IMeetingChatCtrlEvent
{
public:
	virtual ~IMeetingChatCtrlEvent() {}

	/**
	 * @brief Chat message callback. This function is used to inform the user once received the message sent by others.
	 * @param chatMsg An object pointer to the chat message.
	 * @param content A pointer to the chat message in json format. This parameter is currently invalid, hereby only for reservations. 
	 */
	virtual void onChatMsgNotification(IChatMsgInfo* chatMsg, const zchar_t* content = nullptr) = 0;

	/** 
	 * @brief The authority of chat changes callback. This function is used to inform the user when the authority of chat changes in the meeting or webinar.
	 * @param status_ The chat status. For more details, see \link ChatStatus \endlink.
	 */
	virtual void onChatStatusChangedNotification(ChatStatus* status_) = 0;

	/**
	 * @brief Chat message be deleted callback. This function is used to inform the user host/myself the message be deleted.
	 * @param MsgID is the id of the deleted message.
	 * @param deleteBy Indicates by whom the message was deleted.
	 */
	virtual void onChatMsgDeleteNotification(const zchar_t* msgID, SDKChatMessageDeleteType deleteBy) = 0;

	/**
	 * @brief Chat message be edited callback.
	 * @param chatMsg An object pointer to the chat message.
	 */
	virtual void onChatMessageEditNotification(IChatMsgInfo* chatMsg) = 0;

	virtual void onShareMeetingChatStatusChanged(bool isStart) = 0;

	/**
	 * @brief Invoked when start send file.
	 * @param sender The class to send file object.
	 */
	virtual void onFileSendStart(ISDKFileSender* sender) = 0;

	/**
	 * @brief Invoked when receiving a file from another user.
	 * @param receiver The class to receive the file object.
	 */
	virtual void onFileReceived(ISDKFileReceiver* receiver) = 0;

	/**
	 * @brief Invoked when send or receive file status change.
	 * @param info The class to basic transfer information.
	 */
	virtual void onFileTransferProgress(SDKFileTransferInfo* info) = 0;

};

/** 
 * @class IChatMsgInfoBuilder
 * @brief Chat message builder to create ChatMsgInfo objects.
 * @note If there are duplicate styles, the final appearance is determined by the last applied setting.
 */
class IChatMsgInfoBuilder
{
public:
	/**
	 * @brief Sets chat message content.
	 * @param content The chat message's content. 
	 */
	virtual IChatMsgInfoBuilder* SetContent(const zchar_t* content) = 0;

	/**
	 * @brief Sets who will receive the chat message.
	 * @param receiver Specify the user ID to receive the chat message. The message is sent to all participants when the value is zero(0). 
	 */
	virtual IChatMsgInfoBuilder* SetReceiver(unsigned int receiver) = 0;

	/**
	 * @brief Sets the ID of the thread where the message will be posted.
	 * @param threadId Specify the thread ID. 
	 */
	virtual IChatMsgInfoBuilder* SetThreadId(const zchar_t* threadId) = 0;

	/**
	 * @brief Sets the chat message type.
	 * @param type The chat message's type.
	 */
	virtual IChatMsgInfoBuilder* SetMessageType(SDKChatMessageType type) = 0;

	/**
	 * @brief Sets the chat message content quote style and position.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 */
	virtual IChatMsgInfoBuilder* SetQuotePosition(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content quote style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetQuotePosition(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content insert link style.
	 * @param insertLink The segment link url.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetInsertLink(InsertLinkAttrs insertLink, unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content insert link style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetInsertLink(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content font size style.
	 * @param fontSize The segment font size.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetFontSize(FontSizeAttrs fontSize, unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content font size style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetFontSize(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content italic style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetItalic(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content italic style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetItalic(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content bold style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetBold(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content italic style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetBold(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content strikethrough style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetStrikethrough(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content strikethrough style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetStrikethrough(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content bulleted list style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetBulletedList(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content bulleted list style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetBulletedList(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content numbered list style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetNumberedList(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content numbered list style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetNumberedList(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content under line style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetUnderline(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content under line style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetUnderline(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content font color style.
	 * @param color The segment color value.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetFontColor(FontColorAttrs color, unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content font color style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetFontColor(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Sets the chat message content background color style.
	 * @param color The segment color value.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetBackgroundColor(BackgroundColorAttrs color, unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content background color style.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetBackgroundColor(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Increase the chat message content indent style.
	 * @param indent The segment indent value.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* IncreaseIndent(IndentAttrs indent, unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Decrease the chat message content indent style.
	 * @param indent The segment indent value.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* DecreaseIndent(IndentAttrs indent, unsigned int positionStart, unsigned int positionEnd) = 0;


	/**
	 * @brief Sets the chat message content paragraph style.
	 * @param paragraph The segment paragraph value.
	 * Tips: if paragraph.strParagraph is one of the three contents, bold and font size styles will be added internally.
	 * Paragraph_H1 with bold and FontSize_Large
	 * Paragraph_H2 with bold and FontSize_Medium
	 * Paragraph_H3 with bold and FontSize_Small
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* SetParagraph(ParagraphAttrs paragraph, unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear the chat message content paragraph style.
	 * Tips: if paragraph.strParagraph is one of Paragraph_H1, Paragraph_H2, Paragraph_H3,
	 * Bold and font size styles will be removed internally.
	 * @param positionStart The segment start position.
	 * @param positionEnd The segment end position.
	 * @return Return value is a pointer of IChatMsgInfoBuilder with modifications for chain call.
	 */
	virtual IChatMsgInfoBuilder* UnsetParagraph(unsigned int positionStart, unsigned int positionEnd) = 0;

	/**
	 * @brief Clear all set styles.
	 */
	virtual IChatMsgInfoBuilder* ClearStyles() = 0;

	/**
	 * @brief Clear all set properties.
	 */
	virtual IChatMsgInfoBuilder* Clear() = 0;

	/**
	 * @brief build chat message entity.
	 * @return If the function succeeds, it returns the message detail info. Otherwise, this function fails and returns nullptr.
	 */
	virtual IChatMsgInfo* Build() = 0;

	virtual ~IChatMsgInfoBuilder() {}
};

/**
 * @class IMeetingChatController
 * @brief Meeting chat controller interface
 */
class IMeetingChatController
{
public:
	/**
	 * @brief Sets meeting chat callback event.
	 * @param pEvent A pointer to the IMeetingChatCtrlEvent to receive chat callback event.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The event is used by SDK to pass the callback event to user's application. If this function is not called or fails, the user's application can not retrieve the callback event.
	 */
	virtual SDKError SetEvent(IMeetingChatCtrlEvent* pEvent) = 0;

	/**
	 * @brief Gets the authority status to send current message. 
	 * @return If the function succeeds, the return value is a pointer to the structure of ChatStatus. Otherwise, the return value is nullptr.
	 */
	virtual const ChatStatus* GetChatStatus() = 0;

	/**
	 * @brief Sets the chat privilege of participants.
	 * @param privilege The chat privilege of participants.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetParticipantsChatPrivilege(SDKChatPrivilege privilege) = 0;

	/**
	 * @brief Determines whether the legal notice for chat is available
	 * @return true if the legal notice for chat is available. Otherwise, false.
	 */
	virtual bool IsMeetingChatLegalNoticeAvailable() = 0;

	/**
	 * @brief Gets the chat legal notices prompt.
	 */
	virtual const zchar_t* getChatLegalNoticesPrompt() = 0;

	/**
     * @brief Gets the chat legal notices explained.
	 */
	virtual const zchar_t* getChatLegalNoticesExplained() = 0;

	/**
	 * @brief Determines whether the legal notice for sharing in meeting chat is available
	 * @return true if the legal notice for sharing in meeting chat is available. Otherwise, false.
	 */
	virtual bool IsShareMeetingChatLegalNoticeAvailable() = 0;

	/**
	 * @brief Gets the sharing in meeting chat started legal notices content.
	 */
	virtual const zchar_t* GetShareMeetingChatStartedLegalNoticeContent() = 0;

	/**
	 * @brief Gets the sharing in meeting chat stopped legal notices content.
	 */
	virtual const zchar_t* GetShareMeetingChatStoppedLegalNoticeContent() = 0;

	/**
	 * @brief Determines whether the message can be deleted.
	 * @param msgID is the message id.
	 * @return true if the message can be deleted. Otherwise, false.
	 */
	virtual bool IsChatMessageCanBeDeleted(const zchar_t* msgID) = 0;

	/**
	 * @brief Delete chat message by message id.	 
	 * @param msgID is the message id.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError DeleteChatMessage(const zchar_t* msgID) = 0;

	/**
	 * @brief Gets all chat message id.	
	 */
	virtual IList<const zchar_t*>* GetAllChatMessageID() = 0;

	/**
	 * @brief Gets chat message by message ID.	
	 * @param msgID is the message ID.
	 */
	virtual IChatMsgInfo* GetChatMessageById(const zchar_t* msgID) = 0;

	/**
	 * @brief Gets the chat message builder which can help construct the message entity.
	 */
	virtual IChatMsgInfoBuilder* GetChatMessageBuilder() = 0;

	/**
	 * @brief Sends a chat message.
	 * @param msg Specify the message detail info.
	 */
	virtual SDKError SendChatMsgTo(IChatMsgInfo* msg) = 0;

	/**
	 * @brief Determines whether file transfer is enabled.
	 * @return true if file transfer is enabled. Otherwise, false.
	 */	 
	virtual bool IsFileTransferEnabled() = 0;

	/**
	 * @brief Sends file to specify user in current session.
	 * @param filePath The absolute path of the file.
	 * @param user Send the file to this user.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note This interface is related to chat privilege, see \link SDKChatPrivilege \endlink enum.
	 */
	virtual SDKError TransferFile(const zchar_t* filePath, unsigned int userid) = 0;

	/**
	 * @brief Sends file to all users in current session.
	 * @param filePath The absolute path of the file.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note This interface is related to chat privilege, see \link SDKChatPrivilege \endlink enum.
	 */
	virtual SDKError TransferFileToAll(const zchar_t* filePath) = 0;

	/**
	 * @brief Gets the list of allowed file types in transfer.
	 * @return The value of allowed file types in transfer, comma-separated if there are multiple values. Exe files are by default forbidden from being transferred.
	 */
	virtual const zchar_t* GetTransferFileTypeAllowList() = 0;

	/**
	 * @brief Gets the maximum size for file transfer.
	 * @return The maximum number of bytes for file transfer.
	 */
	virtual unsigned long long GetMaxTransferFileSizeBytes() = 0;
};
END_ZOOM_SDK_NAMESPACE
#endif